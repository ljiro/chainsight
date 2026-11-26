"""Port congestion indicators from AIS port calls, and simple, honest forecasts.

Observations (hourly, per port with calls):
  vessels_in_port  vessels with an open AIS-geofence call at the top of the hour
  arrivals         calls whose observed arrival falls in the hour

Forecasts: seasonal naive (same hour yesterday) once 24 h of history exist, otherwise
persistence (last value). Intervals come from the method's own historical absolute errors
for that port; with too little history there is no interval rather than an invented one.
"""

from __future__ import annotations

import json
import statistics
from datetime import datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from ml.common.registry import ensure_rule_version, record_evaluation

SEASONAL_VERSION = "seasonal-naive-24h-v1"
PERSISTENCE_VERSION = "persistence-v1"
HORIZON_H = 24
MIN_ERRORS_FOR_INTERVAL = 24


def _hour(t: datetime) -> datetime:
    return t.replace(minute=0, second=0, microsecond=0)


def build_observations(conn: Connection, now: datetime, lookback_h: int = 48) -> int:
    """Upsert hourly observations for completed hours. Values are what ChainSight knew; a call
    detected late appears only in hours after it was recorded (no backdating)."""
    source = conn.execute(
        sa.text("SELECT id FROM data_sources WHERE key = 'aisstream'")
    ).scalar_one()
    end = _hour(now)
    hours = [end - timedelta(hours=i) for i in range(1, lookback_h + 1)]
    inserted = 0
    for h in hours:
        inserted += conn.execute(
            sa.text(
                """
                INSERT INTO port_observations (port_id, metric, observed_at, source_id, available_at, value)
                SELECT c.port_id, 'vessels_in_port', CAST(:h AS timestamptz), :src, :now, count(*)
                FROM voyage_port_calls c
                WHERE c.created_at <= CAST(:h AS timestamptz)
                  AND (c.actual_departure_at IS NULL OR c.actual_departure_at > CAST(:h AS timestamptz))
                GROUP BY c.port_id
                ON CONFLICT DO NOTHING
                """
            ),
            {"h": h, "src": source, "now": now},
        ).rowcount
        inserted += conn.execute(
            sa.text(
                """
                INSERT INTO port_observations (port_id, metric, observed_at, source_id, available_at, value)
                SELECT c.port_id, 'arrivals', CAST(:h AS timestamptz), :src, :now, count(*)
                FROM voyage_port_calls c
                WHERE c.actual_arrival_at >= CAST(:h AS timestamptz)
                  AND c.actual_arrival_at < CAST(:h AS timestamptz) + interval '1 hour'
                GROUP BY c.port_id
                ON CONFLICT DO NOTHING
                """
            ),
            {"h": h, "src": source, "now": now},
        ).rowcount
    return inserted


def seasonal_naive(history: dict[datetime, float], target: datetime) -> tuple[float | None, str]:
    """Value at the same hour yesterday, else the latest value (persistence)."""
    same_hour = history.get(target - timedelta(hours=24))
    if same_hour is not None:
        return same_hour, SEASONAL_VERSION
    if not history:
        return None, PERSISTENCE_VERSION
    return history[max(history)], PERSISTENCE_VERSION


def forecast(conn: Connection, now: datetime, *, top_ports: int = 20) -> int:
    seasonal_id = ensure_rule_version(
        conn,
        "congestion",
        SEASONAL_VERSION,
        "seasonal naive (24 h)",
        "Same hour yesterday. Rule-based; no training.",
    )
    persistence_id = ensure_rule_version(
        conn,
        "congestion",
        PERSISTENCE_VERSION,
        "persistence",
        "Last observed value; used while history < 24 h.",
    )
    ids = {SEASONAL_VERSION: seasonal_id, PERSISTENCE_VERSION: persistence_id}
    ports = (
        conn.execute(
            sa.text(
                "SELECT port_id FROM port_observations WHERE metric = 'vessels_in_port' "
                "AND observed_at > :since GROUP BY port_id ORDER BY max(value) DESC LIMIT :n"
            ),
            {"since": now - timedelta(hours=48), "n": top_ports},
        )
        .scalars()
        .all()
    )
    rows = []
    base = _hour(now)
    for port_id in ports:
        history = {
            r.observed_at: float(r.value)
            for r in conn.execute(
                sa.text(
                    "SELECT observed_at, value FROM port_observations WHERE port_id = :p "
                    "AND metric = 'vessels_in_port' AND observed_at > :since"
                ),
                {"p": port_id, "since": now - timedelta(days=8)},
            )
        }
        errors = [
            abs(float(r.e))
            for r in conn.execute(
                sa.text(
                    "SELECT predicted_value - actual_value AS e FROM congestion_predictions "
                    "WHERE port_id = :p AND actual_value IS NOT NULL "
                    "ORDER BY target_time DESC LIMIT 500"
                ),
                {"p": port_id},
            )
        ]
        half_width = (
            statistics.quantiles(errors, n=10)[7]
            if len(errors) >= MIN_ERRORS_FOR_INTERVAL
            else None
        )
        for step in range(1, HORIZON_H + 1):
            target = base + timedelta(hours=step)
            known = {t: v for t, v in history.items() if t <= base}
            value, method = seasonal_naive(known, target)
            if value is None:
                continue
            rows.append(
                {
                    "port_id": str(port_id),
                    "target_time": target.isoformat(),
                    "model_version_id": str(ids[method]),
                    "predicted_at": now.isoformat(),
                    "input_data_as_of": base.isoformat(),
                    "predicted_value": value,
                    "lower": max(0.0, value - half_width) if half_width is not None else None,
                    "upper": value + half_width if half_width is not None else None,
                }
            )
    if rows:
        conn.execute(
            sa.text(
                """
                INSERT INTO congestion_predictions (port_id, metric, target_time, model_version_id,
                    predicted_at, input_data_as_of, predicted_value, lower_bound, upper_bound)
                SELECT r.port_id, 'vessels_in_port', r.target_time, r.model_version_id, r.predicted_at,
                    r.input_data_as_of, r.predicted_value, r.lower, r.upper
                FROM jsonb_to_recordset(CAST(:rows AS jsonb)) AS r(port_id uuid, target_time timestamptz,
                    model_version_id uuid, predicted_at timestamptz, input_data_as_of timestamptz,
                    predicted_value real, lower real, upper real)
                """
            ),
            {"rows": json.dumps(rows)},
        )
    return len(rows)


def backfill_and_evaluate(conn: Connection, now: datetime) -> int:
    resolved = conn.execute(
        sa.text(
            """
            UPDATE congestion_predictions c SET actual_value = o.value, resolved_at = :now
            FROM port_observations o
            WHERE c.resolved_at IS NULL AND o.port_id = c.port_id AND o.metric = c.metric
              AND o.observed_at = c.target_time
            """
        ),
        {"now": now},
    ).rowcount
    for r in conn.execute(
        sa.text(
            "SELECT model_version_id, avg(abs(predicted_value - actual_value)) AS mae, count(*) AS n "
            "FROM congestion_predictions WHERE actual_value IS NOT NULL AND resolved_at > :since "
            "GROUP BY model_version_id"
        ),
        {"since": now - timedelta(days=7)},
    ):
        record_evaluation(
            conn,
            r.model_version_id,
            split="live",
            metrics={"mae_vessels": round(float(r.mae), 3)},
            n_samples=int(r.n),
            data_mode="real",
            window=(now - timedelta(days=7), now),
        )
    return resolved
