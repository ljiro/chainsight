"""Read models for the predictions API (bound parameters only)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from core.settings import get_settings

_MODEL_COLS = "m.version AS model_version, m.algorithm AS model_algorithm, m.is_synthetic_trained"


async def models(session: AsyncSession) -> list[dict[str, Any]]:
    rows = await session.execute(
        sa.text(
            """
            SELECT m.id, m.model_name, m.version, m.algorithm, m.status, m.is_synthetic_trained,
                   m.n_train, m.n_test, m.training_window_start, m.training_window_end,
                   m.feature_schema, m.notes, m.created_at,
                   (SELECT jsonb_agg(jsonb_build_object('split', e.split, 'metrics', e.metrics,
                        'n_samples', e.n_samples, 'data_mode', e.data_mode, 'evaluated_at', e.evaluated_at)
                        ORDER BY e.evaluated_at DESC)
                    FROM (SELECT DISTINCT ON (split) * FROM model_evaluations
                          WHERE model_version_id = m.id ORDER BY split, evaluated_at DESC) e) AS evaluations,
                   (SELECT count(*) FROM eta_predictions p WHERE p.model_version_id = m.id)
                   + (SELECT count(*) FROM delay_predictions p WHERE p.model_version_id = m.id)
                   + (SELECT count(*) FROM congestion_predictions p WHERE p.model_version_id = m.id)
                     AS predictions
            FROM model_versions m
            ORDER BY m.model_name, (m.status = 'production') DESC, m.created_at DESC
            """
        )
    )
    return [dict(r._mapping) | {"evaluations": r.evaluations or []} for r in rows]


async def latest_eta(session: AsyncSession, *, since: datetime, limit: int) -> list[dict[str, Any]]:
    rows = await session.execute(
        sa.text(
            f"""
            SELECT DISTINCT ON (p.vessel_id) p.id, p.vessel_id, v.name AS vessel_name, v.mmsi,
                   p.target_unlocode, port.name AS target_port_name, p.predicted_at, p.input_data_as_of,
                   p.predicted_arrival_at, p.lower_bound_at, p.upper_bound_at, p.interval_level,
                   p.remaining_distance_nm, p.features->>'interval_method' AS interval_method,
                   p.features->>'fallback_reason' AS fallback_reason, p.actual_arrival_at, p.error_hours,
                   {_MODEL_COLS}
            FROM eta_predictions p
            JOIN vessels v ON v.id = p.vessel_id
            JOIN model_versions m ON m.id = p.model_version_id
            LEFT JOIN ports port ON port.id = p.target_port_id
            WHERE p.predicted_at > :since
            ORDER BY p.vessel_id, p.predicted_at DESC
            """
        ),
        {"since": since},
    )
    items = [dict(r._mapping) for r in rows]
    items.sort(key=lambda r: r["predicted_arrival_at"])
    return items[:limit]


async def vessel_eta_history(
    session: AsyncSession, vessel_id: uuid.UUID, limit: int = 24
) -> list[dict[str, Any]]:
    rows = await session.execute(
        sa.text(
            f"""
            SELECT p.id, p.vessel_id, NULL AS vessel_name, NULL::int AS mmsi, p.target_unlocode,
                   port.name AS target_port_name, p.predicted_at, p.input_data_as_of, p.predicted_arrival_at,
                   p.lower_bound_at, p.upper_bound_at, p.interval_level, p.remaining_distance_nm,
                   p.features->>'interval_method' AS interval_method,
                   p.features->>'fallback_reason' AS fallback_reason, p.actual_arrival_at, p.error_hours,
                   {_MODEL_COLS}
            FROM eta_predictions p JOIN model_versions m ON m.id = p.model_version_id
            LEFT JOIN ports port ON port.id = p.target_port_id
            WHERE p.vessel_id = :v ORDER BY p.predicted_at DESC LIMIT :limit
            """
        ),
        {"v": vessel_id, "limit": limit},
    )
    return [dict(r._mapping) for r in rows]


_DELAY_SELECT = f"""
SELECT d.id, d.shipment_id, s.reference_value, c.name AS carrier_name, s.destination_unlocode,
       d.predicted_at, d.input_data_as_of, d.delay_probability, d.threshold_hours, d.calibration_method,
       d.actual_delayed, d.actual_delay_hours, ds.data_mode AS shipment_data_mode,
       (SELECT r.factors FROM risk_assessments r WHERE r.shipment_id = d.shipment_id
          AND r.model_version_id = d.model_version_id AND r.assessed_at = d.predicted_at LIMIT 1) AS factors,
       {_MODEL_COLS}
FROM delay_predictions d
JOIN shipments s ON s.id = d.shipment_id
JOIN carriers c ON c.id = s.carrier_id
JOIN data_sources ds ON ds.id = s.source_id
JOIN model_versions m ON m.id = d.model_version_id
"""


async def latest_delay(
    session: AsyncSession, org_id: uuid.UUID, *, limit: int
) -> list[dict[str, Any]]:
    rows = await session.execute(
        sa.text(
            f"SELECT * FROM ({_DELAY_SELECT} WHERE s.actual_arrival_at IS NULL "
            "AND s.organization_id = :org) x "
            "WHERE x.predicted_at = (SELECT max(predicted_at) FROM delay_predictions "
            "WHERE shipment_id = x.shipment_id) "
            "ORDER BY x.delay_probability DESC LIMIT :limit"
        ),
        {"limit": limit, "org": org_id},
    )
    return [dict(r._mapping) | {"factors": r.factors or []} for r in rows]


async def shipment_delay_history(
    session: AsyncSession, org_id: uuid.UUID, shipment_id: uuid.UUID, limit: int = 24
) -> list[dict[str, Any]]:
    rows = await session.execute(
        sa.text(
            f"{_DELAY_SELECT} WHERE d.shipment_id = :s AND s.organization_id = :org "
            "ORDER BY d.predicted_at DESC LIMIT :limit"
        ),
        {"s": shipment_id, "org": org_id, "limit": limit},
    )
    return [dict(r._mapping) | {"factors": r.factors or []} for r in rows]


async def congestion_overview(
    session: AsyncSession, now: datetime, *, limit: int
) -> list[dict[str, Any]]:
    rows = await session.execute(
        sa.text(
            """
            WITH latest_run AS (
                SELECT port_id, max(predicted_at) AS predicted_at FROM congestion_predictions GROUP BY port_id
            )
            SELECT p.id AS port_id, p.unlocode, p.name,
                   (SELECT value FROM port_observations o WHERE o.port_id = p.id
                     AND o.metric = 'vessels_in_port' ORDER BY observed_at DESC LIMIT 1) AS current_value,
                   (SELECT max(observed_at) FROM port_observations o WHERE o.port_id = p.id
                     AND o.metric = 'vessels_in_port') AS observed_at,
                   (SELECT count(DISTINCT observed_at) FROM port_observations o WHERE o.port_id = p.id
                     AND o.metric = 'vessels_in_port') AS history_hours,
                   (SELECT jsonb_agg(jsonb_build_object('target_time', c.target_time,
                        'value', c.predicted_value, 'lower', c.lower_bound, 'upper', c.upper_bound)
                        ORDER BY c.target_time)
                    FROM congestion_predictions c
                    WHERE c.port_id = p.id AND c.predicted_at = lr.predicted_at) AS forecast,
                   (SELECT string_agg(DISTINCT m.version, ' + ') FROM congestion_predictions c
                     JOIN model_versions m ON m.id = c.model_version_id
                     WHERE c.port_id = p.id AND c.predicted_at = lr.predicted_at) AS method,
                   lr.predicted_at
            FROM latest_run lr JOIN ports p ON p.id = lr.port_id
            ORDER BY current_value DESC NULLS LAST LIMIT :limit
            """
        ),
        {"limit": limit},
    )
    return [dict(r._mapping) | {"forecast": r.forecast or []} for r in rows]


async def port_congestion(
    session: AsyncSession, port_id: uuid.UUID, now: datetime
) -> dict[str, Any]:
    obs = await session.execute(
        sa.text(
            "SELECT observed_at, metric, value FROM port_observations WHERE port_id = :p "
            "AND observed_at > :since ORDER BY observed_at"
        ),
        {"p": port_id, "since": now - timedelta(hours=72)},
    )
    observations: dict[str, list[dict[str, Any]]] = {}
    for r in obs:
        observations.setdefault(r.metric, []).append({"t": r.observed_at, "value": r.value})
    overview = [
        o for o in await congestion_overview(session, now, limit=10_000) if o["port_id"] == port_id
    ]
    return {
        "port_id": port_id,
        "observations": observations,
        "forecast": overview[0]["forecast"] if overview else [],
        "method": overview[0]["method"] if overview else None,
    }


async def anomalies(
    session: AsyncSession, *, vessel_id: uuid.UUID | None, status: str, limit: int
) -> list[dict[str, Any]]:
    clauses = ["a.category = 'vessel_anomaly'"]
    params: dict[str, Any] = {"limit": limit}
    if vessel_id is not None:
        clauses.append("a.entity_id = :v")
        params["v"] = vessel_id
    if status == "open":
        clauses.append("a.resolved_at IS NULL")
    elif status == "resolved":
        clauses.append("a.resolved_at IS NOT NULL")
    rows = await session.execute(
        sa.text(
            "SELECT a.id, a.entity_id AS vessel_id, v.name AS vessel_name, a.severity, a.title, a.message, "
            "a.details->>'type' AS type, a.details->'evidence' AS evidence, a.raised_at, a.resolved_at "
            "FROM system_alerts a LEFT JOIN vessels v ON v.id = a.entity_id "
            f"WHERE {' AND '.join(clauses)} ORDER BY a.raised_at DESC LIMIT :limit"
        ),
        params,
    )
    return [dict(r._mapping) | {"evidence": r.evidence or {}} for r in rows]


async def summary(session: AsyncSession, org_id: uuid.UUID, now: datetime) -> dict[str, Any]:
    r = (
        await session.execute(
            sa.text(
                """
                SELECT
                  (SELECT count(DISTINCT vessel_id) FROM eta_predictions
                     WHERE predicted_at > :day) AS eta_vessels,
                  (SELECT count(*) FROM (SELECT DISTINCT ON (shipment_id) delay_probability
                     FROM delay_predictions d JOIN shipments s ON s.id = d.shipment_id
                     WHERE s.actual_arrival_at IS NULL AND s.organization_id = :org
                     ORDER BY shipment_id, predicted_at DESC) x
                     WHERE x.delay_probability >= :high) AS high_risk,
                  (SELECT bool_or(m.is_synthetic_trained) FROM model_versions m WHERE m.model_name = 'delay'
                     AND m.status = 'production') AS delay_model_synthetic,
                  (SELECT count(*) FROM system_alerts
                     WHERE category = 'vessel_anomaly' AND resolved_at IS NULL) AS open_anomalies,
                  (SELECT count(DISTINCT port_id) FROM congestion_predictions WHERE predicted_at > :day)
                     AS congestion_ports,
                  (SELECT count(*) FROM eta_predictions WHERE actual_arrival_at IS NOT NULL) AS eta_resolved
                """
            ),
            {
                "day": now - timedelta(hours=24),
                "high": get_settings().delay_alert_probability,
                "org": org_id,
            },
        )
    ).one()
    return dict(r._mapping)
