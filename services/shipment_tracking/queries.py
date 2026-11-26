"""Read models for shipment and carrier-performance endpoints (bound parameters only)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from services.fleet_tracking.queries import get_vessel

ACTIVE_STATUSES = ("planned", "at_origin", "loaded", "in_transit", "transshipment", "discharged")

_SELECT = """
SELECT s.id, s.reference_type, s.reference_value, s.status, s.planned_arrival_at,
       s.current_eta_at, s.actual_arrival_at, s.delay_hours, s.delay_status, s.last_event_at,
       s.last_refreshed_at, s.origin_unlocode, s.destination_unlocode,
       op.name AS origin_name, dp.name AS destination_name,
       c.code AS carrier_code, c.name AS carrier_name,
       ds.key AS source_key, ds.data_mode,
       COALESCE((SELECT array_agg(ct.container_number ORDER BY ct.container_number)
                 FROM shipment_items si JOIN containers ct ON ct.id = si.container_id
                 WHERE si.shipment_id = s.id), '{}') AS containers
FROM shipments s
JOIN carriers c ON c.id = s.carrier_id
JOIN data_sources ds ON ds.id = s.source_id
LEFT JOIN ports op ON op.id = s.origin_port_id
LEFT JOIN ports dp ON dp.id = s.destination_port_id
"""


def summary(row: Any) -> dict[str, Any]:
    return {
        "id": row.id,
        "carrier": {"code": row.carrier_code, "name": row.carrier_name},
        "reference_type": row.reference_type,
        "reference_value": row.reference_value,
        "origin": {
            "unlocode": row.origin_unlocode,
            "port_name": row.origin_name,
            "mapped": row.origin_name is not None,
        },
        "destination": {
            "unlocode": row.destination_unlocode,
            "port_name": row.destination_name,
            "mapped": row.destination_name is not None,
        },
        "status": row.status,
        "planned_arrival_at": row.planned_arrival_at,
        "current_eta_at": row.current_eta_at,
        "actual_arrival_at": row.actual_arrival_at,
        "delay_hours": float(row.delay_hours) if row.delay_hours is not None else None,
        "delay_status": row.delay_status,
        "last_event_at": row.last_event_at,
        "last_refreshed_at": row.last_refreshed_at,
        "containers": list(row.containers or []),
        "provenance": {"source": row.source_key, "data_mode": row.data_mode},
    }


async def list_shipments(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    q: str | None,
    carrier: str | None,
    status: str | None,
    delay_status: str | None,
    active_only: bool,
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], int]:
    clauses = ["s.organization_id = :org"]
    params: dict[str, Any] = {"org": org_id, "limit": limit, "offset": offset}
    if q:
        clauses.append(
            "(s.reference_value ILIKE :q_like OR EXISTS (SELECT 1 FROM shipment_items si "
            "JOIN containers ct ON ct.id = si.container_id WHERE si.shipment_id = s.id "
            "AND ct.container_number ILIKE :q_like))"
        )
        params["q_like"] = f"%{q.strip().replace(' ', '')}%"
    if carrier:
        clauses.append("c.code = :carrier")
        params["carrier"] = carrier
    if status:
        clauses.append("s.status = :status")
        params["status"] = status
    if delay_status:
        clauses.append("s.delay_status = :delay_status")
        params["delay_status"] = delay_status
    if active_only:
        clauses.append("s.status = ANY(:active)")
        params["active"] = list(ACTIVE_STATUSES)
    where = " WHERE " + " AND ".join(clauses)
    total = (
        await session.execute(
            sa.text(
                f"SELECT count(*) FROM shipments s JOIN carriers c ON c.id = s.carrier_id{where}"
            ),
            params,
        )
    ).scalar_one()
    rows = await session.execute(
        sa.text(
            f"{_SELECT}{where} ORDER BY (s.delay_status = 'delayed') DESC, "
            "s.current_eta_at NULLS LAST, s.updated_at DESC LIMIT :limit OFFSET :offset"
        ),
        params,
    )
    return [summary(r) for r in rows], total


async def get_shipment(
    session: AsyncSession, org_id: uuid.UUID, shipment_id: uuid.UUID, now: datetime
) -> dict[str, Any] | None:
    row = (
        await session.execute(
            sa.text(f"{_SELECT} WHERE s.organization_id = :org AND s.id = :id"),
            {"org": org_id, "id": shipment_id},
        )
    ).one_or_none()
    if row is None:
        return None
    events = await session.execute(
        sa.text(
            "SELECT e.id, e.event_type, e.event_code, e.classifier, e.event_at, e.event_created_at, "
            "e.location_unlocode, e.location_name, ct.container_number, e.vessel_name, e.vessel_imo, "
            "e.voyage_number, e.description, e.raw_code, e.ingested_at FROM shipment_events e "
            "LEFT JOIN containers ct ON ct.id = e.container_id WHERE e.shipment_id = :id "
            "ORDER BY e.event_at, e.classifier"
        ),
        {"id": shipment_id},
    )
    legs = await session.execute(
        sa.text(
            "SELECT sv.leg_sequence AS sequence, sv.carrier_vessel_name, sv.carrier_vessel_imo, "
            "sv.carrier_voyage_number, sv.load_unlocode, sv.discharge_unlocode, sv.vessel_id "
            "FROM shipment_vessels sv WHERE sv.shipment_id = :id ORDER BY sv.leg_sequence"
        ),
        {"id": shipment_id},
    )
    leg_out = []
    for leg in legs:
        position = None
        if leg.vessel_id is not None:
            vessel = await get_vessel(session, leg.vessel_id, now)
            position = vessel["position"] if vessel else None
        leg_out.append(dict(leg._mapping) | {"vessel_position": position})
    integration = (
        await session.execute(
            sa.text(
                "SELECT ci.status FROM carrier_integrations ci JOIN carriers c ON c.id = ci.carrier_id "
                "WHERE c.code = :code AND ci.organization_id IS NULL "
                "AND ci.capability = 'shipment_tracking'"
            ),
            {"code": row.carrier_code},
        )
    ).scalar_one_or_none()
    return summary(row) | {
        "events": [dict(e._mapping) for e in events],
        "legs": leg_out,
        "integration_status": integration,
        "refreshable": row.source_key != "csv-import",
    }


async def shipments_summary(session: AsyncSession, org_id: uuid.UUID) -> dict[str, Any]:
    params = {"org": org_id, "active": list(ACTIVE_STATUSES)}
    counts = (
        await session.execute(
            sa.text(
                "SELECT count(*) AS total, count(*) FILTER (WHERE status = ANY(:active)) AS active, "
                "count(*) FILTER (WHERE delay_status = 'delayed') AS delayed, "
                "count(*) FILTER (WHERE delay_status = 'arrived_late') AS arrived_late "
                "FROM shipments WHERE organization_id = :org"
            ),
            params,
        )
    ).one()

    async def grouped(sql: str) -> dict[str, int]:
        return {str(r.k): int(r.n) for r in await session.execute(sa.text(sql), params)}

    return {
        "total": counts.total,
        "active": counts.active,
        "delayed": counts.delayed,
        "arrived_late": counts.arrived_late,
        "by_status": await grouped(
            "SELECT status AS k, count(*) AS n FROM shipments WHERE organization_id = :org GROUP BY 1"
        ),
        "by_delay_status": await grouped(
            "SELECT delay_status AS k, count(*) AS n FROM shipments WHERE organization_id = :org "
            "GROUP BY 1"
        ),
        "by_data_mode": await grouped(
            "SELECT ds.data_mode AS k, count(*) AS n FROM shipments s JOIN data_sources ds "
            "ON ds.id = s.source_id WHERE s.organization_id = :org GROUP BY 1"
        ),
    }


async def carrier_performance(session: AsyncSession, org_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = await session.execute(
        sa.text(
            """
            SELECT c.code, c.name, c.carrier_type,
                   ci.status AS integration_status, ci.status_reason AS integration_reason,
                   last_call.occurred_at AS last_call_at, last_call.outcome AS last_call_outcome,
                   (SELECT count(DISTINCT h.vessel_id) FROM vessel_carrier_history h
                     WHERE h.carrier_id = c.id AND h.valid_to IS NULL) AS vessels_associated,
                   count(s.id) AS shipments_tracked,
                   count(s.id) FILTER (WHERE s.status = ANY(:active)) AS shipments_active,
                   count(s.id) FILTER (WHERE s.delay_status = 'delayed') AS shipments_delayed,
                   count(s.id) FILTER (WHERE s.actual_arrival_at IS NOT NULL) AS arrivals,
                   count(s.id) FILTER (WHERE s.delay_status = 'arrived_on_time') AS on_time,
                   avg(s.delay_hours) FILTER (WHERE s.actual_arrival_at IS NOT NULL) AS avg_delay
            FROM carriers c
            LEFT JOIN carrier_integrations ci ON ci.carrier_id = c.id
                 AND ci.organization_id IS NULL AND ci.capability = 'shipment_tracking'
            LEFT JOIN LATERAL (
                SELECT l.occurred_at, l.outcome FROM carrier_integration_logs l
                WHERE l.carrier_integration_id = ci.id ORDER BY l.occurred_at DESC LIMIT 1
            ) last_call ON true
            LEFT JOIN shipments s ON s.carrier_id = c.id AND s.organization_id = :org
            GROUP BY c.id, ci.status, ci.status_reason, last_call.occurred_at, last_call.outcome
            ORDER BY (c.code = 'demo'), c.name
            """
        ),
        {"org": org_id, "active": list(ACTIVE_STATUSES)},
    )
    out = []
    for r in rows:
        d = dict(r._mapping)
        on_time = d.pop("on_time")
        avg = d.pop("avg_delay")
        d["on_time_rate"] = round(on_time / d["arrivals"], 3) if d["arrivals"] else None
        d["avg_arrival_delay_hours"] = round(float(avg), 1) if avg is not None else None
        out.append(d)
    return out
