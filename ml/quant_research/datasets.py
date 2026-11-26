"""Stored feature datasets (`research_datasets` + `maritime_features`).

A dataset is a named list of feature keys. Building it writes each feature's history with
its computed `available_at`. A rebuild that changes a stored value adds a new version
available from the rebuild time; earlier versions are kept, so stored history is never
rewritten after the fact.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd
import sqlalchemy as sa
from sqlalchemy.engine import Engine

from core.logging_config import get_logger
from core.timeutils import utcnow
from ml.quant_research.features import COMPOSITES, FeatureError, FeatureSpec, compute_feature

log = get_logger(__name__)
REL_TOL = 1e-9
_INSERT_FEATURES = sa.text(
    """
    INSERT INTO maritime_features (feature_key, as_of, version, value, available_at, dataset_id)
    SELECT :k, r.t, r.v, r.value, r.avail, :d FROM jsonb_to_recordset(CAST(:rows AS jsonb))
        AS r(t timestamptz, v smallint, value double precision, avail timestamptz)
    ON CONFLICT DO NOTHING
    """
)


def default_weekly_features() -> list[str]:
    keys = []
    for c in COMPOSITES:
        keys += [f"{c}|W|level", f"{c}|W|yoy", f"{c}|W|z26"]
    # Hormuz, Suez, Cape of Good Hope, Panama: total transits.
    for n in (6, 1, 7, 2):
        keys.append(f"portwatch:chokepoint:chokepoint{n}:n_total|W|level")
    return keys


@dataclass(frozen=True)
class DatasetDefinition:
    key: str
    name: str
    description: str
    frequency: str
    features: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {"features": list(self.features), "frequency": self.frequency,
                "first_release_only": True}  # fmt: skip

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(json.dumps(self.as_dict(), sort_keys=True).encode()).hexdigest()


DEFAULT_DATASET = DatasetDefinition(
    key="maritime-weekly-v1",
    name="Maritime indicators, weekly",
    description="Composite chokepoint and port indicators from IMF PortWatch, weekly (Fri), "
    "as levels, year-over-year changes and 26-week z-scores. First-release values only.",
    frequency="W",
    features=tuple(default_weekly_features()),
)


def build_dataset(
    engine: Engine, ds: DatasetDefinition = DEFAULT_DATASET, now: datetime | None = None
) -> dict[str, int]:
    now = now or utcnow()
    with engine.begin() as conn:
        dataset_id = conn.execute(
            sa.text(
                """
                INSERT INTO research_datasets (key, name, description, frequency, definition, content_hash)
                VALUES (:k, :n, :d, :f, CAST(:def AS jsonb), :h)
                ON CONFLICT (key) DO UPDATE SET name = EXCLUDED.name, description = EXCLUDED.description,
                    definition = EXCLUDED.definition, content_hash = EXCLUDED.content_hash, updated_at = now()
                RETURNING id
                """
            ),
            {"k": ds.key, "n": ds.name, "d": ds.description, "f": ds.frequency,
             "def": json.dumps(ds.as_dict()), "h": ds.content_hash},
        ).scalar_one()  # fmt: skip
    counts = {"features": 0, "inserted": 0, "new_versions": 0, "skipped": 0}
    for key in ds.features:
        try:
            with engine.connect() as conn:
                feat = compute_feature(conn, FeatureSpec.parse(key))
        except FeatureError as exc:
            log.warning("feature_skipped", feature=key, reason=str(exc))
            counts["skipped"] += 1
            continue
        with engine.begin() as conn:
            stored = {
                r.as_of: (r.version, r.value)
                for r in conn.execute(
                    sa.text(
                        "SELECT DISTINCT ON (as_of) as_of, version, value FROM maritime_features "
                        "WHERE feature_key = :k ORDER BY as_of, version DESC"
                    ),
                    {"k": key},
                )
            }
            rows = []
            for r in feat.itertuples(index=False):
                as_of = pd.Timestamp(r.as_of).to_pydatetime()
                prev = stored.get(as_of)
                if prev is None:
                    rows.append({"t": as_of.isoformat(), "v": 1, "value": float(r.value),
                                 "avail": pd.Timestamp(r.available_at).isoformat()})  # fmt: skip
                    counts["inserted"] += 1
                elif abs(prev[1] - float(r.value)) > REL_TOL * max(1.0, abs(prev[1])):
                    avail = max(pd.Timestamp(r.available_at).to_pydatetime(), now)
                    rows.append({"t": as_of.isoformat(), "v": prev[0] + 1, "value": float(r.value),
                                 "avail": avail.isoformat()})  # fmt: skip
                    counts["new_versions"] += 1
            if rows:
                conn.execute(
                    _INSERT_FEATURES,
                    {"k": key, "d": dataset_id, "rows": json.dumps(rows)},
                )
        counts["features"] += 1
    log.info("dataset_built", dataset=ds.key, **counts)
    return counts
