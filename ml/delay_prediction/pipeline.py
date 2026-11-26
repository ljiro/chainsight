"""Delay probability: training (baseline vs calibrated GBM), prediction, outcomes, live eval."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd
import sqlalchemy as sa
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sqlalchemy.engine import Connection, Engine

from core.logging_config import get_logger
from ml.common.metrics import calibration_bins, classification_metrics
from ml.common.registry import (
    ModelCard,
    dataset_hash,
    load_artifact,
    log_to_tracking,
    new_version,
    production_version,
    promote,
    record_evaluation,
    register,
    save_artifact,
    tracking_run,
)
from ml.common.splits import time_split
from ml.delay_prediction.features import FEATURES, EventRecord, delay_features, risk_factors

log = get_logger(__name__)
SNAPSHOT_FRACTIONS = (0.1, 0.3, 0.5, 0.7, 0.9)


class InsufficientData(Exception):
    pass


def frame_from_journeys(
    journeys: list[tuple[str, list[EventRecord], str | None, str, datetime, float]],
    threshold_hours: float,
) -> pd.DataFrame:
    """journeys: (key, events, origin, destination, actual_arrival, actual_delay_hours).
    Snapshots are taken at fractions of the planned transit, before the arrival."""
    rows = []
    for key, events, origin, dest, actual_arrival, delay_h in journeys:
        base = delay_features(events, as_of=actual_arrival, origin=origin, destination=dest)
        if base is None:
            continue
        plan_start = (
            actual_arrival - timedelta(hours=delay_h) - timedelta(days=base.planned_transit_days)
        )
        for frac in SNAPSHOT_FRACTIONS:
            as_of = plan_start + timedelta(days=base.planned_transit_days * frac)
            if as_of >= actual_arrival:
                continue
            f = delay_features(events, as_of=as_of, origin=origin, destination=dest)
            if f is not None:
                rows.append(
                    {
                        **f.as_dict(),
                        "as_of": as_of,
                        "shipment_key": key,
                        "label": int(delay_h > threshold_hours),
                    }
                )
    return pd.DataFrame(rows)


@dataclass(frozen=True)
class TrainResult:
    model_version_id: uuid.UUID
    baseline_version_id: uuid.UUID
    promoted: bool
    test_metrics: dict[str, Any]
    baseline_metrics: dict[str, Any]
    n_shipments: int


def train(
    engine: Engine,
    frame: pd.DataFrame,
    *,
    synthetic: bool,
    min_shipments: int,
    threshold_hours: float,
    promote_synthetic: bool = False,
    artifact_dir: str | None = None,
) -> TrainResult:
    n_ship = frame["shipment_key"].nunique() if not frame.empty else 0
    if n_ship < min_shipments:
        raise InsufficientData(
            f"only {n_ship} shipments with observed arrivals; need {min_shipments}"
        )
    split = time_split(frame, "as_of", test_fraction=0.25)
    X_tr, y_tr = split.train[FEATURES], split.train["label"]
    X_te, y_te = split.test[FEATURES], split.test["label"]
    if y_tr.nunique() < 2:
        raise InsufficientData("training labels contain a single class")

    baseline = make_pipeline(
        StandardScaler(), LogisticRegression(class_weight="balanced", max_iter=1000)
    )
    baseline.fit(X_tr, y_tr)
    # Class imbalance: balanced class weights; probabilities re-calibrated (isotonic, 3-fold).
    gbm = CalibratedClassifierCV(
        HistGradientBoostingClassifier(
            class_weight="balanced", max_iter=200, learning_rate=0.05, random_state=7
        ),
        method="isotonic",
        cv=3,
    )
    gbm.fit(X_tr, y_tr)
    p_base = baseline.predict_proba(X_te)[:, 1]
    p_gbm = gbm.predict_proba(X_te)[:, 1]
    base_metrics = classification_metrics(y_te, p_base)
    test: dict[str, Any] = {
        **classification_metrics(y_te, p_gbm),
        "calibration": calibration_bins(y_te, p_gbm),
    }
    data_mode = "synthetic" if synthetic else "real"
    hp = {
        "gbm": {"max_iter": 200, "learning_rate": 0.05, "class_weight": "balanced"},
        "calibration": "isotonic_cv3",
        "threshold_hours": threshold_hours,
    }
    common = dict(
        is_synthetic_trained=synthetic,
        dataset_hash=dataset_hash(frame[[*FEATURES, "label"]]),
        training_window_start=split.train["as_of"].min().to_pydatetime(warn=False),
        training_window_end=split.train["as_of"].max().to_pydatetime(warn=False),
        n_train=len(split.train),
        n_test=len(split.test),
        feature_schema={
            "features": FEATURES,
            "label": f"arrival > original plan + {threshold_hours} h",
        },
    )
    card = ModelCard(
        model_name="delay",
        version=new_version("delay-gbm"),
        algorithm="HistGradientBoosting + isotonic calibration",
        hyperparameters=hp,
        notes=f"{n_ship} {'SYNTHETIC (demo carrier)' if synthetic else 'observed'} shipments.",
        **common,
    )
    base_card = ModelCard(
        model_name="delay",
        version=new_version("delay-logreg"),
        algorithm="Logistic regression (balanced)",
        hyperparameters={"class_weight": "balanced"},
        notes="Baseline.",
        **common,
    )
    with tracking_run("chainsight-delay", card.version) as run:
        log_to_tracking(
            run,
            params={
                "n_train": len(split.train),
                "synthetic": synthetic,
                "threshold": threshold_hours,
            },
            metrics={f"test_{k}": v for k, v in test.items() if isinstance(v, int | float)}
            | {f"baseline_{k}": v for k, v in base_metrics.items() if isinstance(v, int | float)},
            tags={"data_mode": data_mode},
        )
        card.mlflow_run_id = run.info.run_id if run is not None else None
    card.artifact_uri = save_artifact(gbm, card, artifact_dir)
    base_card.artifact_uri = save_artifact(baseline, base_card, artifact_dir)
    gbm_better = float(test["pr_auc"] or 0) >= float(base_metrics["pr_auc"] or 0)
    window = (
        split.test["as_of"].min().to_pydatetime(warn=False),
        split.test["as_of"].max().to_pydatetime(warn=False),
    )
    with engine.begin() as conn:
        vid = register(conn, card)
        bid = register(conn, base_card)
        record_evaluation(
            conn,
            vid,
            split="test",
            metrics=test,
            n_samples=len(split.test),
            data_mode=data_mode,
            window=window,
        )
        record_evaluation(
            conn,
            bid,
            split="test",
            metrics=base_metrics,
            n_samples=len(split.test),
            data_mode=data_mode,
            window=window,
        )
        winner = vid if gbm_better else bid
        promoted = (not synthetic) or promote_synthetic
        if promoted:
            promote(conn, winner)
    log.info(
        "delay_trained",
        version=card.version,
        pr_auc=test["pr_auc"],
        baseline_pr_auc=base_metrics["pr_auc"],
        synthetic=synthetic,
        promoted=promoted,
    )
    return TrainResult(vid, bid, promoted, test, base_metrics, n_ship)


def shipment_events(conn: Connection, shipment_id: Any) -> list[EventRecord]:
    return [
        EventRecord(
            r.event_code,
            r.classifier,
            r.event_at,
            r.ingested_at,
            r.location_unlocode,
            r.vessel_name,
        )
        for r in conn.execute(
            sa.text(
                "SELECT event_code, classifier, event_at, ingested_at, location_unlocode, vessel_name "
                "FROM shipment_events WHERE shipment_id = :s"
            ),
            {"s": shipment_id},
        )
    ]


def predict_live(conn: Connection, now: datetime, *, threshold_hours: float) -> int:
    prod = production_version(conn, "delay")
    if prod is None or prod.artifact_uri is None:
        return 0
    model = load_artifact(prod.artifact_uri)
    # Guardrail: a model trained on synthetic data may only score synthetic shipments.
    synthetic_only = bool(prod.is_synthetic_trained)
    shipments = conn.execute(
        sa.text(
            "SELECT s.id, s.origin_unlocode, s.destination_unlocode FROM shipments s "
            "JOIN data_sources ds ON ds.id = s.source_id "
            "WHERE (NOT CAST(:synthetic_only AS boolean) OR ds.data_mode = 'synthetic') "
            "AND s.status NOT IN ('delivered', 'unknown') AND s.actual_arrival_at IS NULL "
            "AND s.destination_unlocode IS NOT NULL AND NOT EXISTS (SELECT 1 FROM delay_predictions d "
            "WHERE d.shipment_id = s.id AND d.predicted_at > :recent)"
        ),
        {"recent": now - timedelta(minutes=55), "synthetic_only": synthetic_only},
    ).all()
    n = 0
    for s in shipments:
        events = shipment_events(conn, s.id)
        if not events:
            continue
        as_of = max(e.available_at for e in events)
        f = delay_features(
            events,
            as_of=min(as_of, now),
            origin=s.origin_unlocode,
            destination=s.destination_unlocode,
        )
        if f is None:
            continue
        p = float(model.predict_proba(pd.DataFrame([f.as_dict()])[FEATURES])[0, 1])
        conn.execute(
            sa.text(
                "INSERT INTO delay_predictions (shipment_id, model_version_id, predicted_at, "
                "input_data_as_of, delay_probability, threshold_hours, calibration_method, features) "
                "VALUES (:s, :m, :now, :as_of, :p, :th, :cal, CAST(:f AS jsonb))"
            ),
            {
                "s": s.id,
                "m": prod.id,
                "now": now,
                "as_of": as_of,
                "p": p,
                "th": threshold_hours,
                "cal": "isotonic" if "gbm" in prod.version else None,
                "f": json.dumps(
                    f.as_dict()
                    | {"model": prod.version, "synthetic_trained": prod.is_synthetic_trained}
                ),
            },
        )
        level = "high" if p >= 0.7 else "medium" if p >= 0.4 else "low"
        conn.execute(
            sa.text(
                "INSERT INTO risk_assessments (shipment_id, model_version_id, assessed_at, score, "
                "level, factors) VALUES (:s, :m, :now, :score, :level, CAST(:factors AS jsonb))"
            ),
            {
                "s": s.id,
                "m": prod.id,
                "now": now,
                "score": round(p, 3),
                "level": level,
                "factors": json.dumps(risk_factors(f)),
            },
        )
        n += 1
    return n


def backfill_outcomes(conn: Connection, now: datetime) -> int:
    return conn.execute(
        sa.text(
            """
            UPDATE delay_predictions d SET actual_delayed = (s.delay_hours > d.threshold_hours),
                actual_delay_hours = s.delay_hours, resolved_at = :now
            FROM shipments s
            WHERE d.shipment_id = s.id AND d.resolved_at IS NULL AND s.actual_arrival_at IS NOT NULL
              AND s.delay_hours IS NOT NULL
            """
        ),
        {"now": now},
    ).rowcount


def evaluate_live(conn: Connection, now: datetime, window: timedelta = timedelta(days=60)) -> int:
    rows = conn.execute(
        sa.text(
            "SELECT d.model_version_id, d.delay_probability, d.actual_delayed, ds.data_mode "
            "FROM delay_predictions d JOIN shipments s ON s.id = d.shipment_id "
            "JOIN data_sources ds ON ds.id = s.source_id "
            "WHERE d.actual_delayed IS NOT NULL AND d.resolved_at > :since"
        ),
        {"since": now - window},
    ).all()
    by_model: dict[Any, list[Any]] = {}
    for r in rows:
        by_model.setdefault(r.model_version_id, []).append(r)
    for model_id, rs in by_model.items():
        modes = {r.data_mode for r in rs}
        mode = (
            "synthetic"
            if modes == {"synthetic"}
            else "real"
            if "synthetic" not in modes
            else "mixed"
        )
        y = np.array([int(r.actual_delayed) for r in rs])
        p = np.array([float(r.delay_probability) for r in rs])
        record_evaluation(
            conn,
            model_id,
            split="live",
            metrics=classification_metrics(y, p),
            n_samples=len(rs),
            data_mode=mode,
            window=(now - window, now),
        )
    return len(by_model)
