"""Model registry (model_versions / model_evaluations), artifacts and MLflow tracking."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import joblib
import pandas as pd
import sqlalchemy as sa
from sqlalchemy.engine import Connection

from core.logging_config import get_logger
from core.settings import get_settings
from core.timeutils import utcnow

log = get_logger(__name__)


def dataset_hash(df: pd.DataFrame) -> str:
    """Content hash of a training dataset (column order and values), for lineage."""
    payload = pd.util.hash_pandas_object(df.reindex(sorted(df.columns), axis=1), index=False).values
    return hashlib.sha256(payload.tobytes() + ",".join(sorted(df.columns)).encode()).hexdigest()


@dataclass
class ModelCard:
    model_name: str
    version: str
    algorithm: str
    is_synthetic_trained: bool
    feature_schema: dict[str, Any]
    hyperparameters: dict[str, Any] = field(default_factory=dict)
    dataset_hash: str | None = None
    training_window_start: datetime | None = None
    training_window_end: datetime | None = None
    n_train: int | None = None
    n_test: int | None = None
    notes: str | None = None
    mlflow_run_id: str | None = None
    artifact_uri: str | None = None


def new_version(prefix: str) -> str:
    return f"{prefix}-{utcnow():%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"


class ArtifactUnavailable(FileNotFoundError):
    """A registered model's artifact is not present where this process looks for it."""


def save_artifact(model: Any, card: ModelCard, directory: str | None = None) -> str:
    """Write the artifact and return its URI. Under the configured artifact directory the URI
    is relative to it, so a model trained on one machine (or container) resolves on another
    that mounts the same directory; an explicit `directory` yields an absolute path."""
    root = Path(directory or get_settings().model_artifact_dir)
    rel = Path(card.model_name) / f"{card.version}.joblib"
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": model, "card": card.__dict__}, root / rel)
    return str((root / rel).resolve()) if directory else rel.as_posix()


def artifact_path(uri: str) -> Path:
    p = Path(uri)
    return p if p.is_absolute() else Path(get_settings().model_artifact_dir) / p


def load_artifact(uri: str) -> Any:
    path = artifact_path(uri)
    if not path.is_file():
        raise ArtifactUnavailable(f"model artifact not found: {path}")
    return joblib.load(path)["model"]


def register(conn: Connection, card: ModelCard, *, status: str = "candidate") -> uuid.UUID:
    row_id: uuid.UUID = conn.execute(
        sa.text(
            "INSERT INTO model_versions (model_name, version, algorithm, status, mlflow_run_id, "
            "dataset_hash, training_window_start, training_window_end, feature_schema, "
            "hyperparameters, n_train, n_test, is_synthetic_trained, artifact_uri, notes) VALUES "
            "(:model_name, :version, :algorithm, :status, :mlflow_run_id, :dataset_hash, "
            ":training_window_start, :training_window_end, CAST(:feature_schema AS jsonb), "
            "CAST(:hyperparameters AS jsonb), :n_train, :n_test, :is_synthetic_trained, "
            ":artifact_uri, :notes) RETURNING id"
        ),
        {
            **card.__dict__,
            "status": status,
            "feature_schema": json.dumps(card.feature_schema),
            "hyperparameters": json.dumps(card.hyperparameters, default=str),
        },
    ).scalar_one()
    return row_id


def promote(conn: Connection, model_version_id: uuid.UUID) -> None:
    """Make one version production; archive the previous production version of that model."""
    name = conn.execute(
        sa.text("SELECT model_name FROM model_versions WHERE id = :id"), {"id": model_version_id}
    ).scalar_one()
    conn.execute(
        sa.text(
            "UPDATE model_versions SET status = 'archived' WHERE model_name = :n "
            "AND status = 'production' AND id <> :id"
        ),
        {"n": name, "id": model_version_id},
    )
    conn.execute(
        sa.text("UPDATE model_versions SET status = 'production' WHERE id = :id"),
        {"id": model_version_id},
    )


def production_version(conn: Connection, model_name: str) -> sa.Row[Any] | None:
    return conn.execute(
        sa.text(
            "SELECT id, version, algorithm, artifact_uri, is_synthetic_trained, feature_schema "
            "FROM model_versions WHERE model_name = :n AND status = 'production' "
            "ORDER BY created_at DESC LIMIT 1"
        ),
        {"n": model_name},
    ).one_or_none()


def ensure_rule_version(
    conn: Connection, model_name: str, version: str, algorithm: str, notes: str
) -> uuid.UUID:
    """Register a deterministic (non-trained) model such as a physics baseline, idempotently.
    Rule models are not trained on any data, so they are not 'synthetic-trained'."""
    existing = conn.execute(
        sa.text("SELECT id FROM model_versions WHERE model_name = :n AND version = :v"),
        {"n": model_name, "v": version},
    ).scalar_one_or_none()
    if existing:
        return existing  # type: ignore[no-any-return]
    vid = register(
        conn,
        ModelCard(model_name, version, algorithm, False, {"type": "rule"}, notes=notes),
        status="candidate",
    )
    if production_version(conn, model_name) is None:
        promote(conn, vid)
    return vid


def record_evaluation(
    conn: Connection,
    model_version_id: uuid.UUID,
    *,
    split: str,
    metrics: dict[str, Any],
    n_samples: int,
    data_mode: str,
    window: tuple[datetime | None, datetime | None] = (None, None),
) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO model_evaluations (model_version_id, split, window_start, window_end, "
            "metrics, n_samples, data_mode) VALUES (:id, :split, :ws, :we, CAST(:m AS jsonb), :n, :mode)"
        ),
        {
            "id": model_version_id,
            "split": split,
            "ws": window[0],
            "we": window[1],
            "m": json.dumps(metrics, default=str),
            "n": n_samples,
            "mode": data_mode,
        },
    )


@contextmanager
def tracking_run(experiment: str, run_name: str) -> Iterator[Any]:
    """MLflow run if a tracking URI is configured; otherwise a no-op recorder."""
    uri = get_settings().mlflow_tracking_uri
    if not uri:
        yield None
        return
    os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
    import mlflow

    mlflow.set_tracking_uri(uri)
    mlflow.set_experiment(experiment)
    with mlflow.start_run(run_name=run_name) as run:
        yield run


def log_to_tracking(
    run: Any, *, params: dict[str, Any], metrics: dict[str, Any], tags: dict[str, str]
) -> None:
    if run is None:
        return
    import mlflow

    mlflow.log_params({k: str(v)[:250] for k, v in params.items()})
    mlflow.log_metrics({k: float(v) for k, v in metrics.items() if isinstance(v, int | float)})
    mlflow.set_tags(tags)
