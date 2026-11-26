"""Persist AIS-geofence port calls and the voyages between them."""

from __future__ import annotations

import json
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from services.fleet_tracking.detection import GeofenceObservation, PortEvent, detect_port_events


@dataclass(frozen=True, slots=True)
class Fix:
    vessel_id: uuid.UUID
    observed_at: datetime
    lat: float
    lon: float
    sog_knots: float | None
    nav_status: int | None


_LOCATE = sa.text(
    """
    SELECT r.i, a.port_id
    FROM jsonb_to_recordset(CAST(:rows AS jsonb)) AS r(i int, lon float8, lat float8)
    LEFT JOIN LATERAL (
        SELECT pa.port_id FROM port_areas pa JOIN ports p ON p.id = pa.port_id
        WHERE ST_Intersects(pa.area, ST_SetSRID(ST_MakePoint(r.lon, r.lat), 4326)::geography)
        ORDER BY ST_Distance(p.location, ST_SetSRID(ST_MakePoint(r.lon, r.lat), 4326)::geography)
        LIMIT 1
    ) a ON true
    """
)
_OPEN_CALLS = sa.text(
    "SELECT vessel_id, port_id FROM voyage_port_calls WHERE vessel_id = ANY(:ids) "
    "AND detection_method = 'ais_geofence' AND actual_departure_at IS NULL"
)


async def process_port_calls(
    session: AsyncSession,
    fixes: list[Fix],
    source_id: uuid.UUID,
    new_vessels: set[uuid.UUID] = frozenset(),  # type: ignore[assignment]
) -> list[tuple[uuid.UUID, PortEvent]]:
    """Detect and persist arrivals/departures for a batch of fixes. Returns applied events.

    For a vessel seen for the first time while already stopped in port, the arrival time
    is unknown (it arrived before we started observing it), so `actual_arrival_at` is
    left NULL rather than set to the first observation time."""
    if not fixes:
        return []
    located = await session.execute(
        _LOCATE,
        {"rows": json.dumps([{"i": i, "lon": f.lon, "lat": f.lat} for i, f in enumerate(fixes)])},
    )
    port_by_index = {r.i: r.port_id for r in located}
    vessel_ids = list({f.vessel_id for f in fixes})
    open_calls = {
        r.vessel_id: r.port_id for r in await session.execute(_OPEN_CALLS, {"ids": vessel_ids})
    }

    per_vessel: dict[uuid.UUID, list[GeofenceObservation]] = defaultdict(list)
    for i, f in enumerate(fixes):
        per_vessel[f.vessel_id].append(
            GeofenceObservation(f.observed_at, port_by_index.get(i), f.sog_knots, f.nav_status)
        )

    applied: list[tuple[uuid.UUID, PortEvent]] = []
    for vessel_id, observations in per_vessel.items():
        events, _ = detect_port_events(open_calls.get(vessel_id), observations)
        first_seen = min(o.observed_at for o in observations)
        for event in events:
            unknown_arrival = (
                vessel_id in new_vessels and event.kind == "arrival" and event.at == first_seen
            )
            await _apply(session, vessel_id, event, source_id, unknown_arrival=unknown_arrival)
            applied.append((vessel_id, event))
    return applied


async def _apply(
    session: AsyncSession,
    vessel_id: uuid.UUID,
    event: PortEvent,
    source_id: uuid.UUID,
    *,
    unknown_arrival: bool,
) -> None:
    params = {"vid": vessel_id, "port": event.port_id, "at": event.at, "source_id": source_id}
    if event.kind == "arrival":
        voyage_id = (
            await session.execute(
                sa.text(
                    "UPDATE voyages SET destination_port_id = :port, arrived_at = :at, "
                    "status = 'completed', updated_at = now() WHERE id = ("
                    "SELECT id FROM voyages WHERE vessel_id = :vid AND status = 'underway' "
                    "ORDER BY departed_at DESC NULLS LAST LIMIT 1) RETURNING id"
                ),
                params,
            )
        ).scalar_one_or_none()
        await session.execute(
            sa.text(
                "INSERT INTO voyage_port_calls (vessel_id, voyage_id, port_id, actual_arrival_at, "
                "detection_method, source_id) VALUES (:vid, :voyage, :port, :arrival, "
                "'ais_geofence', :source_id)"
            ),
            {**params, "voyage": voyage_id, "arrival": None if unknown_arrival else event.at},
        )
    else:
        await session.execute(
            sa.text(
                "UPDATE voyage_port_calls SET actual_departure_at = :at WHERE vessel_id = :vid "
                "AND port_id = :port AND actual_departure_at IS NULL "
                "AND detection_method = 'ais_geofence'"
            ),
            params,
        )
        await session.execute(
            sa.text(
                "INSERT INTO voyages (vessel_id, origin_port_id, departed_at, status, source_id) "
                "VALUES (:vid, :port, :at, 'underway', :source_id)"
            ),
            params,
        )
