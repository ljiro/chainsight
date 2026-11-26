"""ETA dataset building (point-in-time), training, live prediction and outcome back-fill.

Training labels come from AIS-observed arrivals (voyage_port_calls with a known
actual_arrival_at). For each arrival, snapshots are taken 1-72 h before it, and each
snapshot uses only positions observed at or before the snapshot time.
"""

from __future__ import annotations

import json
import statistics
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd
import sqlalchemy as sa
from sqlalchemy.engine import Connection, Engine

from core.logging_config import get_logger
from ml.common.destinations import resolve_destination
from ml.common.metrics import eta_metrics
from ml.common.registry import (
    ModelCard,
    dataset_hash,
    ensure_rule_version,
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
from ml.eta_prediction.features import FEATURES, build_features
from ml.eta_prediction.model import (
    BASELINE_VERSION,
    BaselineEtaModel,
    EtaModel,
    QuantileGbmEtaModel,
)

log = get_logger(__name__)
SNAPSHOT_OFFSETS_H = (1, 2, 4, 8, 12, 24, 48, 72)
MIN_DISTANCE_NM, MAX_DISTANCE_NM = 5.0, 8000.0
# Rows implying faster than this are physically impossible for merchant ships (MMSI
# collisions, spoofed positions, mis-detected calls) and are excluded from training.
MAX_IMPLIED_SPEED_KN = 40.0


def drop_implausible(frame: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    if frame.empty:
        return frame, 0
    implied = frame["remaining_distance_nm"] / frame["label_hours"].clip(lower=1e-6)
    keep = implied <= MAX_IMPLIED_SPEED_KN
    return frame[keep].copy(), int((~keep).sum())


class InsufficientData(Exception):
    pass


def _snapshot(conn: Connection, vessel_id: Any, at: datetime) -> Any:
    return conn.execute(
        sa.text(
            """
            SELECT p.observed_at, ST_Y(p.location::geometry) AS lat, ST_X(p.location::geometry) AS lon,
                   p.sog_knots, p.cog_deg,
                   (SELECT avg(q.sog_knots) FROM vessel_positions q WHERE q.vessel_id = p.vessel_id
                      AND q.observed_at BETWEEN :at - interval '2 hours' AND :at) AS recent_sog
            FROM vessel_positions p
            WHERE p.vessel_id = :vid AND p.observed_at <= :at AND p.observed_at > :at - interval '30 minutes'
            ORDER BY p.observed_at DESC LIMIT 1
            """
        ),
        {"vid": vessel_id, "at": at},
    ).one_or_none()


def build_training_frame(conn: Connection, *, since: datetime | None = None) -> pd.DataFrame:
    arrivals = conn.execute(
        sa.text(
            """
            SELECT c.vessel_id, c.actual_arrival_at, ST_Y(p.location::geometry) AS plat,
                   ST_X(p.location::geometry) AS plon, v.length_m, v.vessel_class
            FROM voyage_port_calls c JOIN ports p ON p.id = c.port_id JOIN vessels v ON v.id = c.vessel_id
            WHERE c.actual_arrival_at IS NOT NULL AND c.detection_method = 'ais_geofence'
              AND (CAST(:since AS timestamptz) IS NULL OR c.actual_arrival_at >= :since)
            """
        ),
        {"since": since},
    ).all()
    rows: list[dict[str, Any]] = []
    for a in arrivals:
        for offset in SNAPSHOT_OFFSETS_H:
            at = a.actual_arrival_at - timedelta(hours=offset)
            snap = _snapshot(conn, a.vessel_id, at)
            if snap is None:
                continue
            f = build_features(
                at=at,
                lat=snap.lat,
                lon=snap.lon,
                sog=snap.sog_knots,
                cog=snap.cog_deg,
                recent_mean_sog=float(snap.recent_sog) if snap.recent_sog is not None else None,
                target_lat=a.plat,
                target_lon=a.plon,
                length_m=a.length_m,
                vessel_class=a.vessel_class,
            )
            if not MIN_DISTANCE_NM <= f.remaining_distance_nm <= MAX_DISTANCE_NM:
                continue
            rows.append(
                {
                    **f.as_dict(),
                    "snapshot_at": at,
                    "voyage_key": f"{a.vessel_id}:{a.actual_arrival_at.isoformat()}",
                    "label_hours": (a.actual_arrival_at - at).total_seconds() / 3600,
                }
            )
    return pd.DataFrame(rows)


@dataclass(frozen=True)
class TrainResult:
    model_version_id: uuid.UUID
    promoted: bool
    test_metrics: dict[str, Any]
    baseline_metrics: dict[str, Any]
    n_voyages: int


def train(
    engine: Engine,
    *,
    min_voyages: int,
    frame: pd.DataFrame | None = None,
    synthetic: bool = False,
    artifact_dir: str | None = None,
) -> TrainResult:
    """Fit the quantile GBM, compare with the baseline on a chronological test split, and
    promote it only if it beats the baseline. `frame`+`synthetic` support explicit dev runs."""
    if frame is None:
        with engine.connect() as conn:
            frame = build_training_frame(conn)
    frame, dropped = drop_implausible(frame)
    n_voyages = frame["voyage_key"].nunique() if not frame.empty else 0
    if n_voyages < min_voyages:
        raise InsufficientData(
            f"only {n_voyages} AIS-observed arrivals with usable history; need {min_voyages}. "
            "Keep the AIS consumer running and retry, or train explicitly on synthetic data."
        )
    split = time_split(frame, "snapshot_at", test_fraction=0.25)
    model = QuantileGbmEtaModel().fit(split.train, split.train["label_hours"])
    preds = model.predict(split.test)
    test = eta_metrics(
        split.test["label_hours"],
        [p.hours for p in preds],
        [p.lower_hours if p.lower_hours is not None else np.nan for p in preds],
        [p.upper_hours if p.upper_hours is not None else np.nan for p in preds],
    )
    base = BaselineEtaModel().predict(split.test)
    base_metrics = eta_metrics(split.test["label_hours"], [p.hours for p in base])
    data_mode = "synthetic" if synthetic else "real"
    card = ModelCard(
        model_name="eta",
        version=new_version("eta-qgbm"),
        algorithm="HistGradientBoosting quantile p10/p50/p90",
        is_synthetic_trained=synthetic,
        feature_schema={
            "features": FEATURES,
            "label": "hours_to_arrival",
            # Trees cannot extrapolate: inference outside this domain falls back to the baseline.
            "training_domain": {
                "max_label_hours": round(float(frame["label_hours"].max()), 2),
                "max_distance_nm": round(float(frame["remaining_distance_nm"].max()), 1),
            },
        },
        hyperparameters=model.params,
        dataset_hash=dataset_hash(frame[[*FEATURES, "label_hours"]]),
        training_window_start=split.train["snapshot_at"].min().to_pydatetime(warn=False),
        training_window_end=split.train["snapshot_at"].max().to_pydatetime(warn=False),
        n_train=len(split.train),
        n_test=len(split.test),
        notes=(
            f"Trained on {n_voyages} {'SYNTHETIC' if synthetic else 'AIS-observed'} voyages; "
            f"{dropped} physically implausible rows (> {MAX_IMPLIED_SPEED_KN:.0f} kn implied) excluded."
        ),
    )
    with tracking_run("chainsight-eta", card.version) as run:
        log_to_tracking(
            run,
            params=card.hyperparameters | {"n_train": card.n_train, "synthetic": synthetic},
            metrics={f"test_{k}": v for k, v in test.items() if v is not None}
            | {f"baseline_{k}": v for k, v in base_metrics.items() if v is not None},
            tags={"data_mode": data_mode},
        )
        card.mlflow_run_id = run.info.run_id if run is not None else None
    card.artifact_uri = save_artifact(model, card, artifact_dir)
    beats = (test["mae_hours"] or 1e9) < (base_metrics["mae_hours"] or 1e9)
    with engine.begin() as conn:
        base_id = ensure_rule_version(
            conn,
            "eta",
            BASELINE_VERSION,
            "great-circle distance / recent SOG",
            "Physics baseline; no training data.",
        )
        vid = register(conn, card)
        window = (
            split.test["snapshot_at"].min().to_pydatetime(warn=False),
            split.test["snapshot_at"].max().to_pydatetime(warn=False),
        )
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
            base_id,
            split="test",
            metrics=base_metrics,
            n_samples=len(split.test),
            data_mode=data_mode,
            window=window,
        )
        # Never auto-promote a synthetic-trained model over real-data infrastructure.
        promoted = beats and not synthetic
        if promoted:
            promote(conn, vid)
    log.info(
        "eta_trained",
        version=card.version,
        promoted=promoted,
        mae=test["mae_hours"],
        baseline_mae=base_metrics["mae_hours"],
        synthetic=synthetic,
    )
    return TrainResult(vid, promoted, test, base_metrics, n_voyages)


_CANDIDATES = sa.text(
    """
    SELECT v.id, v.destination, v.length_m, v.vessel_class, v.last_seen_at,
           ST_Y(v.last_location::geometry) AS lat, ST_X(v.last_location::geometry) AS lon,
           v.last_sog_knots, v.last_cog_deg,
           (SELECT avg(q.sog_knots) FROM vessel_positions q WHERE q.vessel_id = v.id
              AND q.observed_at > v.last_seen_at - interval '2 hours') AS recent_sog
    FROM vessels v
    WHERE v.last_seen_at > :fresh AND v.last_sog_knots >= 3 AND v.destination IS NOT NULL
      AND (EXISTS (SELECT 1 FROM vessel_carrier_history h WHERE h.vessel_id = v.id AND h.valid_to IS NULL)
           OR EXISTS (SELECT 1 FROM shipment_vessels sv WHERE sv.vessel_id = v.id))
      AND NOT EXISTS (SELECT 1 FROM eta_predictions e WHERE e.vessel_id = v.id AND e.predicted_at > :recent)
    """
)


@dataclass(frozen=True)
class ActiveEtaModel:
    model_id: uuid.UUID
    model: EtaModel
    version: str
    baseline_id: uuid.UUID
    domain: dict[str, float] | None  # None for the baseline (valid everywhere)

    def choose(self, f: dict[str, float | int]) -> tuple[uuid.UUID, EtaModel, str, str | None]:
        """Use the trained model only inside its training domain (10 % margin)."""
        if self.domain is None:
            return self.model_id, self.model, self.version, None
        if (
            float(f["baseline_hours"]) > 1.1 * self.domain["max_label_hours"]
            or float(f["remaining_distance_nm"]) > 1.1 * self.domain["max_distance_nm"]
        ):
            return self.baseline_id, BaselineEtaModel(), BASELINE_VERSION, "out_of_training_domain"
        return self.model_id, self.model, self.version, None


def _active_model(conn: Connection) -> ActiveEtaModel:
    baseline_id = ensure_rule_version(
        conn,
        "eta",
        BASELINE_VERSION,
        "great-circle distance / recent SOG",
        "Physics baseline; no training data.",
    )
    prod = production_version(conn, "eta")
    if prod is None or prod.artifact_uri is None:
        return ActiveEtaModel(baseline_id, BaselineEtaModel(), BASELINE_VERSION, baseline_id, None)
    # A trained model without a recorded domain is never trusted: always fall back.
    domain = (prod.feature_schema or {}).get("training_domain") or {
        "max_label_hours": 0.0,
        "max_distance_nm": 0.0,
    }
    return ActiveEtaModel(
        prod.id, load_artifact(prod.artifact_uri), prod.version, baseline_id, domain
    )


def predict_live(conn: Connection, now: datetime) -> int:
    """Predict arrival at the AIS-reported destination for watched, underway vessels."""
    active = _active_model(conn)
    ports = conn.execute(
        sa.text(
            "SELECT id, unlocode, upper(name) AS name, ST_Y(location::geometry) AS lat, "
            "ST_X(location::geometry) AS lon FROM ports WHERE unlocode IS NOT NULL"
        )
    ).all()
    codes = {p.unlocode for p in ports}
    counts: dict[str, int] = {}
    for p in ports:
        counts[p.name] = counts.get(p.name, 0) + 1
    names = {p.name: p.unlocode for p in ports if counts[p.name] == 1}
    by_code = {p.unlocode: p for p in ports}
    rows = []
    for v in conn.execute(
        _CANDIDATES, {"fresh": now - timedelta(hours=1), "recent": now - timedelta(minutes=55)}
    ):
        code, method = resolve_destination(v.destination, codes, names)
        if code is None:
            continue
        port = by_code[code]
        f = build_features(
            at=v.last_seen_at,
            lat=v.lat,
            lon=v.lon,
            sog=v.last_sog_knots,
            cog=v.last_cog_deg,
            recent_mean_sog=float(v.recent_sog) if v.recent_sog is not None else None,
            target_lat=port.lat,
            target_lon=port.lon,
            length_m=v.length_m,
            vessel_class=v.vessel_class,
        )
        if not MIN_DISTANCE_NM <= f.remaining_distance_nm <= MAX_DISTANCE_NM:
            continue
        model_id, model, version, fallback = active.choose(f.as_dict())
        est = model.predict(pd.DataFrame([f.as_dict()]))[0]
        base = v.last_seen_at  # predictions are anchored on the input data time
        rows.append(
            {
                "vessel_id": str(v.id),
                "target_port_id": str(port.id),
                "target_unlocode": code,
                "model_version_id": str(model_id),
                "predicted_at": now.isoformat(),
                "input_data_as_of": base.isoformat(),
                "predicted_arrival_at": (base + timedelta(hours=est.hours)).isoformat(),
                "lower_bound_at": (base + timedelta(hours=est.lower_hours)).isoformat()
                if est.lower_hours is not None
                else None,
                "upper_bound_at": (base + timedelta(hours=est.upper_hours)).isoformat()
                if est.upper_hours is not None
                else None,
                "interval_level": est.interval_level,
                "remaining_distance_nm": f.remaining_distance_nm,
                "features": (  # a dict: the whole batch is JSON-encoded once
                    f.as_dict()
                    | {
                        "destination_text": v.destination,
                        "destination_resolution": method,
                        "interval_method": est.interval_method,
                        "model": version,
                        "fallback_reason": fallback,
                    }
                ),
            }
        )
    if rows:
        conn.execute(
            sa.text(
                """
                INSERT INTO eta_predictions (vessel_id, target_port_id, target_unlocode, model_version_id,
                    predicted_at, input_data_as_of, predicted_arrival_at, lower_bound_at, upper_bound_at,
                    interval_level, remaining_distance_nm, features)
                SELECT r.vessel_id, r.target_port_id, r.target_unlocode, r.model_version_id, r.predicted_at,
                    r.input_data_as_of, r.predicted_arrival_at, r.lower_bound_at, r.upper_bound_at,
                    r.interval_level, r.remaining_distance_nm, r.features
                FROM jsonb_to_recordset(CAST(:rows AS jsonb)) AS r(vessel_id uuid, target_port_id uuid,
                    target_unlocode text, model_version_id uuid, predicted_at timestamptz,
                    input_data_as_of timestamptz, predicted_arrival_at timestamptz,
                    lower_bound_at timestamptz, upper_bound_at timestamptz, interval_level numeric,
                    remaining_distance_nm real, features jsonb)
                """
            ),
            {"rows": json.dumps(rows)},
        )
    return len(rows)


def backfill_outcomes(
    conn: Connection, now: datetime, *, expire_after: timedelta = timedelta(days=30)
) -> int:
    """Fill actual arrival + error once the vessel is observed arriving at the target port.
    Predictions whose arrival is never observed expire unresolved-in-fact (actual stays NULL)."""
    resolved = conn.execute(
        sa.text(
            """
            UPDATE eta_predictions e SET actual_arrival_at = sub.arrival,
                error_hours = EXTRACT(EPOCH FROM e.predicted_arrival_at - sub.arrival) / 3600,
                resolved_at = :now
            FROM (
                SELECT p.id, (SELECT min(c.actual_arrival_at) FROM voyage_port_calls c
                              WHERE c.vessel_id = p.vessel_id AND c.port_id = p.target_port_id
                                AND c.actual_arrival_at > p.input_data_as_of) AS arrival
                FROM eta_predictions p WHERE p.resolved_at IS NULL
            ) sub
            WHERE e.id = sub.id AND sub.arrival IS NOT NULL
            """
        ),
        {"now": now},
    ).rowcount
    conn.execute(
        sa.text(
            "UPDATE eta_predictions SET resolved_at = :now "
            "WHERE resolved_at IS NULL AND predicted_at < :cutoff"
        ),
        {"now": now, "cutoff": now - expire_after},
    )
    return resolved


def evaluate_live(conn: Connection, now: datetime, window: timedelta = timedelta(days=30)) -> int:
    """Live metrics per model version over predictions resolved with an observed arrival."""
    rows = conn.execute(
        sa.text(
            "SELECT e.model_version_id, e.error_hours, "
            "EXTRACT(EPOCH FROM e.upper_bound_at - e.actual_arrival_at) AS up, "
            "EXTRACT(EPOCH FROM e.actual_arrival_at - e.lower_bound_at) AS lo FROM eta_predictions e "
            "WHERE e.actual_arrival_at IS NOT NULL AND e.resolved_at > :since"
        ),
        {"since": now - window},
    ).all()
    by_model: dict[Any, list[Any]] = {}
    for r in rows:
        by_model.setdefault(r.model_version_id, []).append(r)
    for model_id, rs in by_model.items():
        errors = [float(r.error_hours) for r in rs]
        metrics = eta_metrics([0.0] * len(errors), errors)
        covered = [r for r in rs if r.up is not None and r.lo is not None]
        if covered:
            metrics["interval_coverage"] = round(
                sum(1 for r in covered if r.up >= 0 and r.lo >= 0) / len(covered), 4
            )
        metrics["median_signed_error_hours"] = round(statistics.median(errors), 3)
        record_evaluation(
            conn,
            model_id,
            split="live",
            metrics=metrics,
            n_samples=len(rs),
            data_mode="real",
            window=(now - window, now),
        )
    return len(by_model)


def synthetic_frame(n_voyages: int = 400, seed: int = 11) -> pd.DataFrame:
    """Synthetic voyages for development only (flagged is_synthetic_trained when used)."""
    rng = np.random.default_rng(seed)
    rows = []
    start = pd.Timestamp("2026-01-01", tz="UTC")
    for i in range(n_voyages):
        speed = rng.uniform(10, 20)
        for offset in SNAPSHOT_OFFSETS_H:
            distance = speed * offset * rng.uniform(0.95, 1.25)  # detours make true time longer
            sog = speed + rng.normal(0, 1.5)
            at = start + pd.Timedelta(hours=i * 6 + rng.uniform(0, 5))
            rows.append(
                {
                    "remaining_distance_nm": distance,
                    "sog_knots": sog,
                    "recent_mean_sog_knots": speed,
                    "bearing_offset_deg": abs(rng.normal(0, 15)),
                    "baseline_hours": distance / max(speed, 1),
                    "length_m": float(rng.choice([200, 300, 366])),
                    "is_tanker": int(rng.random() < 0.2),
                    "hour_utc": at.hour,
                    "day_of_week": at.weekday(),
                    "snapshot_at": at,
                    "voyage_key": f"syn-{i}",
                    "label_hours": offset + rng.normal(0, offset * 0.08),
                }
            )
    return pd.DataFrame(rows)
