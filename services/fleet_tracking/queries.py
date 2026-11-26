"""Read models for the fleet API. Filters are composed from fixed SQL fragments with bound
parameters only; no user input is ever interpolated into SQL text."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession

from services.fleet_tracking.detection import is_approaching

NAV_STATUS_LABELS = {
    0: "Under way using engine",
    1: "At anchor",
    2: "Not under command",
    3: "Restricted manoeuvrability",
    4: "Constrained by draught",
    5: "Moored",
    6: "Aground",
    7: "Engaged in fishing",
    8: "Under way sailing",
    11: "Towing astern",
    12: "Pushing ahead / towing alongside",
    14: "AIS-SART active",
    15: "Not defined",
}
LIVE_S, RECENT_S = 600, 3600


def position_freshness(age_s: float) -> str:
    return "live" if age_s <= LIVE_S else "recent" if age_s <= RECENT_S else "stale"


@dataclass(frozen=True)
class BBox:
    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float

    @classmethod
    def parse(cls, raw: str) -> BBox:
        try:
            parts = [float(p) for p in raw.split(",")]
        except ValueError as exc:
            raise ValueError("bbox must be four numbers: minLon,minLat,maxLon,maxLat") from exc
        if len(parts) != 4:
            raise ValueError("bbox must be four numbers: minLon,minLat,maxLon,maxLat")
        b = cls(*parts)
        if not (-180 <= b.min_lon <= 180 and -180 <= b.max_lon <= 180):
            raise ValueError("bbox longitudes must be within [-180, 180]")
        if not (-90 <= b.min_lat <= b.max_lat <= 90):
            raise ValueError("bbox latitudes must be within [-90, 90] and min <= max")
        return b

    @property
    def crosses_antimeridian(self) -> bool:
        return self.min_lon > self.max_lon

    def contains(self, lat: float, lon: float) -> bool:
        if not self.min_lat <= lat <= self.max_lat:
            return False
        if self.crosses_antimeridian:
            return lon >= self.min_lon or lon <= self.max_lon
        return self.min_lon <= lon <= self.max_lon

    def sql(self, column: str, params: dict[str, Any]) -> str:
        """Envelope filter on a geography column (split in two across the antimeridian)."""
        params.update(
            bb_min_lon=self.min_lon, bb_min_lat=self.min_lat,
            bb_max_lon=self.max_lon, bb_max_lat=self.max_lat,
        )  # fmt: skip
        env = "ST_MakeEnvelope({a}, :bb_min_lat, {b}, :bb_max_lat, 4326)"
        geom = f"{column}::geometry"
        if not self.crosses_antimeridian:
            return f"{geom} && " + env.format(a=":bb_min_lon", b=":bb_max_lon")
        east = env.format(a=":bb_min_lon", b="180")
        west = env.format(a="-180", b=":bb_max_lon")
        return f"({geom} && {east} OR {geom} && {west})"


@dataclass
class VesselFilters:
    bbox: BBox | None = None
    classes: Sequence[str] = ()
    carrier: str | None = None
    q: str | None = None
    seen_within_s: int | None = None
    data_mode: str | None = None
    has_position: bool = False

    def where(self, now: datetime) -> tuple[str, dict[str, Any]]:
        clauses: list[str] = []
        params: dict[str, Any] = {}
        if self.has_position or self.bbox is not None:
            clauses.append("v.last_location IS NOT NULL")
        if self.bbox is not None:
            clauses.append(self.bbox.sql("v.last_location", params))
        if self.classes:
            clauses.append("v.vessel_class = ANY(:classes)")
            params["classes"] = list(self.classes)
        if self.carrier:
            clauses.append("c.code = :carrier")
            params["carrier"] = self.carrier
        if self.q:
            clauses.append(
                "(v.name ILIKE :q_like OR CAST(v.mmsi AS text) = :q OR CAST(v.imo AS text) = :q)"
            )
            params.update(q=self.q.strip(), q_like=f"%{self.q.strip()}%")
        if self.seen_within_s is not None:
            clauses.append("v.last_seen_at >= :seen_after")
            params["seen_after"] = now - timedelta(seconds=self.seen_within_s)
        if self.data_mode:
            clauses.append("ds.data_mode = :data_mode")
            params["data_mode"] = self.data_mode
        return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


_VESSEL_FROM = """
FROM vessels v
LEFT JOIN data_sources ds ON ds.id = v.last_position_source_id
LEFT JOIN LATERAL (
    SELECT h.carrier_id, h.relation, h.method, h.confidence, h.valid_from
    FROM vessel_carrier_history h WHERE h.vessel_id = v.id AND h.valid_to IS NULL
    ORDER BY h.confidence DESC, h.valid_from DESC LIMIT 1
) h ON true
LEFT JOIN carriers c ON c.id = h.carrier_id
"""
_VESSEL_COLUMNS = """
v.id, v.mmsi, v.imo, v.name, v.vessel_class, v.vessel_class_source, v.destination,
ST_Y(v.last_location::geometry) AS lat, ST_X(v.last_location::geometry) AS lon,
v.last_sog_knots, v.last_cog_deg, v.last_heading_deg, v.last_nav_status, v.last_seen_at,
ds.key AS source_key, ds.data_mode,
c.code AS carrier_code, c.name AS carrier_name, h.relation, h.method, h.confidence,
h.valid_from AS carrier_valid_from
"""


def vessel_summary(row: Row[Any], now: datetime) -> dict[str, Any]:
    position = None
    if row.lat is not None and row.last_seen_at is not None:
        age = max(0.0, (now - row.last_seen_at).total_seconds())
        position = {
            "lat": row.lat,
            "lon": row.lon,
            "sog_knots": row.last_sog_knots,
            "cog_deg": row.last_cog_deg,
            "heading_deg": row.last_heading_deg,
            "nav_status": row.last_nav_status,
            "nav_status_label": NAV_STATUS_LABELS.get(row.last_nav_status)
            if row.last_nav_status is not None
            else None,
            "observed_at": row.last_seen_at,
            "age_seconds": round(age, 1),
            "freshness": position_freshness(age),
            "provenance": {"source": row.source_key, "data_mode": row.data_mode},
        }
    carrier = None
    if row.carrier_code:
        carrier = {
            "carrier_code": row.carrier_code,
            "carrier_name": row.carrier_name,
            "relation": row.relation,
            "method": row.method,
            "confidence": float(row.confidence),
            "valid_from": row.carrier_valid_from,
        }
    return {
        "id": row.id,
        "mmsi": row.mmsi,
        "imo": row.imo,
        "name": row.name,
        "vessel_class": row.vessel_class,
        "vessel_class_source": row.vessel_class_source,
        "destination": row.destination,
        "carrier": carrier,
        "position": position,
    }


async def list_vessels(
    session: AsyncSession, f: VesselFilters, now: datetime, *, limit: int, offset: int
) -> tuple[list[dict[str, Any]], int]:
    where, params = f.where(now)
    total = (
        await session.execute(sa.text(f"SELECT count(*) {_VESSEL_FROM}{where}"), params)
    ).scalar_one()
    rows = await session.execute(
        sa.text(
            f"SELECT {_VESSEL_COLUMNS} {_VESSEL_FROM}{where} "
            "ORDER BY v.last_seen_at DESC NULLS LAST, v.id LIMIT :limit OFFSET :offset"
        ),
        {**params, "limit": limit, "offset": offset},
    )
    return [vessel_summary(r, now) for r in rows], total


async def fleet_positions(
    session: AsyncSession, f: VesselFilters, now: datetime, *, limit: int
) -> tuple[list[dict[str, Any]], bool]:
    f.has_position = True
    where, params = f.where(now)
    rows = (
        await session.execute(
            sa.text(
                f"SELECT {_VESSEL_COLUMNS} {_VESSEL_FROM}{where} "
                "ORDER BY v.last_seen_at DESC LIMIT :limit"
            ),
            {**params, "limit": limit + 1},
        )
    ).all()
    return [vessel_summary(r, now) for r in rows[:limit]], len(rows) > limit


async def get_vessel(
    session: AsyncSession, vessel_id: uuid.UUID, now: datetime
) -> dict[str, Any] | None:
    row = (
        await session.execute(
            sa.text(
                f"SELECT {_VESSEL_COLUMNS}, v.call_sign, v.ship_type_code, v.length_m, v.beam_m, "
                f"v.draught_m, v.first_seen_at {_VESSEL_FROM} WHERE v.id = :id"
            ),
            {"id": vessel_id},
        )
    ).one_or_none()
    if row is None:
        return None
    detail = vessel_summary(row, now) | {
        "call_sign": row.call_sign,
        "ship_type_code": row.ship_type_code,
        "length_m": row.length_m,
        "beam_m": row.beam_m,
        "draught_m": row.draught_m,
        "first_seen_at": row.first_seen_at,
    }
    identifiers = await session.execute(
        sa.text(
            "SELECT i.scheme, i.value, i.valid_from, i.valid_to, ds.key AS source "
            "FROM vessel_identifiers i LEFT JOIN data_sources ds ON ds.id = i.source_id "
            "WHERE i.vessel_id = :id ORDER BY i.scheme, i.valid_from DESC"
        ),
        {"id": vessel_id},
    )
    history = await session.execute(
        sa.text(
            "SELECT c.code AS carrier_code, c.name AS carrier_name, h.relation, h.method, "
            "h.confidence, h.valid_from FROM vessel_carrier_history h "
            "JOIN carriers c ON c.id = h.carrier_id WHERE h.vessel_id = :id ORDER BY h.valid_from DESC"
        ),
        {"id": vessel_id},
    )
    calls, _ = await port_calls(
        session, vessel_id=vessel_id, open_only=True, limit=1, offset=0, now=now
    )
    detail["identifiers"] = [dict(r._mapping) for r in identifiers]
    detail["carrier_history"] = [
        dict(r._mapping) | {"confidence": float(r.confidence)} for r in history
    ]
    detail["open_port_call"] = calls[0] if calls else None
    return detail


@dataclass
class Track:
    total_points: int
    points: list[dict[str, Any]]
    gaps: list[dict[str, Any]]
    segments: list[list[list[float]]]
    sources: list[dict[str, Any]] = field(default_factory=list)


async def vessel_track(
    session: AsyncSession,
    vessel_id: uuid.UUID,
    start: datetime,
    end: datetime,
    *,
    max_points: int,
    gap_threshold_s: int,
) -> Track:
    params = {"id": vessel_id, "start": start, "end": end}
    window = "vessel_id = :id AND observed_at BETWEEN :start AND :end"
    window_p = "p.vessel_id = :id AND p.observed_at BETWEEN :start AND :end"
    total = (
        await session.execute(
            sa.text(f"SELECT count(*) FROM vessel_positions WHERE {window}"), params
        )
    ).scalar_one()
    step = max(1, -(-total // max_points))  # ceil division
    rows = await session.execute(
        sa.text(
            f"""
            SELECT observed_at AS t, ST_Y(location::geometry) AS lat, ST_X(location::geometry) AS lon,
                   sog_knots, cog_deg, quality_flags
            FROM (SELECT *, row_number() OVER (ORDER BY observed_at) AS rn, count(*) OVER () AS n
                  FROM vessel_positions WHERE {window}) p
            WHERE (rn - 1) % :step = 0 OR rn = n
            ORDER BY observed_at
            """
        ),
        {**params, "step": step},
    )
    points = [dict(r._mapping) for r in rows]
    gap_rows = await session.execute(
        sa.text(
            f"""
            SELECT prev AS start, observed_at AS "end",
                   EXTRACT(EPOCH FROM observed_at - prev) AS duration_seconds
            FROM (SELECT observed_at, lag(observed_at) OVER (ORDER BY observed_at) AS prev
                  FROM vessel_positions WHERE {window}) g
            WHERE prev IS NOT NULL AND observed_at - prev > make_interval(secs => :gap)
            ORDER BY observed_at
            """
        ),
        {**params, "gap": gap_threshold_s},
    )
    gaps = [dict(r._mapping) | {"duration_seconds": float(r.duration_seconds)} for r in gap_rows]
    sources = await session.execute(
        sa.text(
            "SELECT DISTINCT ds.key AS source, ds.data_mode FROM vessel_positions p "
            f"JOIN data_sources ds ON ds.id = p.source_id WHERE {window_p}"
        ),
        params,
    )

    gap_ends = {g["end"] for g in gaps}
    segments: list[list[list[float]]] = []
    current: list[list[float]] = []
    previous_t: datetime | None = None
    for p in points:
        # A gap lies between two raw points; with downsampling, break when one was skipped over.
        if previous_t is not None and any(previous_t < e <= p["t"] for e in gap_ends):
            if len(current) > 1:
                segments.append(current)
            current = []
        current.append([p["lon"], p["lat"]])
        previous_t = p["t"]
    if len(current) > 1:
        segments.append(current)
    return Track(total, points, gaps, segments, [dict(r._mapping) for r in sources])


_CALL_SELECT = """
SELECT c.id, c.vessel_id, v.name AS vessel_name, c.actual_arrival_at, c.actual_departure_at,
       c.detection_method, ds.key AS source,
       p.id AS port_id, p.unlocode, p.name AS port_name, p.country_code
FROM voyage_port_calls c
JOIN ports p ON p.id = c.port_id
JOIN vessels v ON v.id = c.vessel_id
LEFT JOIN data_sources ds ON ds.id = c.source_id
"""


def _call(row: Row[Any], now: datetime) -> dict[str, Any]:
    duration = None
    if row.actual_arrival_at is not None:
        duration = ((row.actual_departure_at or now) - row.actual_arrival_at).total_seconds()
    return {
        "id": row.id,
        "vessel_id": row.vessel_id,
        "vessel_name": row.vessel_name,
        "actual_arrival_at": row.actual_arrival_at,
        "actual_departure_at": row.actual_departure_at,
        "duration_seconds": duration,
        "detection_method": row.detection_method,
        "source": row.source,
        "port": {
            "id": row.port_id,
            "unlocode": row.unlocode,
            "name": row.port_name,
            "country_code": row.country_code,
        },
    }


async def port_calls(
    session: AsyncSession,
    *,
    now: datetime,
    vessel_id: uuid.UUID | None = None,
    port_id: uuid.UUID | None = None,
    open_only: bool = False,
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], int]:
    clauses: list[str] = []
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if vessel_id is not None:
        clauses.append("c.vessel_id = :vessel_id")
        params["vessel_id"] = vessel_id
    if port_id is not None:
        clauses.append("c.port_id = :port_id")
        params["port_id"] = port_id
    if open_only:
        clauses.append("c.actual_departure_at IS NULL")
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    total = (
        await session.execute(sa.text(f"SELECT count(*) FROM voyage_port_calls c{where}"), params)
    ).scalar_one()
    rows = await session.execute(
        sa.text(
            f"{_CALL_SELECT}{where} ORDER BY COALESCE(c.actual_arrival_at, c.created_at) DESC "
            "LIMIT :limit OFFSET :offset"
        ),
        params,
    )
    return [_call(r, now) for r in rows], total


_PORT_COLUMNS = """
p.id, p.unlocode, p.name, p.country_code,
ST_Y(p.location::geometry) AS lat, ST_X(p.location::geometry) AS lon,
(SELECT max(a.radius_m) FROM port_areas a WHERE a.port_id = p.id) AS geofence_radius_m,
(SELECT count(*) FROM voyage_port_calls c WHERE c.port_id = p.id
   AND c.actual_departure_at IS NULL) AS vessels_in_port
"""


async def list_ports(
    session: AsyncSession, *, bbox: BBox | None, q: str | None, active_only: bool, limit: int
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: dict[str, Any] = {"limit": limit}
    if bbox is not None:
        clauses.append(bbox.sql("p.location", params))
    if q:
        clauses.append("(p.name ILIKE :q_like OR p.unlocode = upper(:q))")
        params.update(q=q.strip(), q_like=f"%{q.strip()}%")
    if active_only:
        clauses.append(
            "EXISTS (SELECT 1 FROM voyage_port_calls c WHERE c.port_id = p.id "
            "AND COALESCE(c.actual_arrival_at, c.created_at) > now() - interval '30 days')"
        )
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = await session.execute(
        sa.text(
            f"SELECT {_PORT_COLUMNS} FROM ports p{where} ORDER BY vessels_in_port DESC, p.name "
            "LIMIT :limit"
        ),
        params,
    )
    return [dict(r._mapping) for r in rows]


async def get_port(
    session: AsyncSession, port_id: uuid.UUID, now: datetime
) -> dict[str, Any] | None:
    row = (
        await session.execute(
            sa.text(
                f"SELECT {_PORT_COLUMNS}, ds.key AS source FROM ports p "
                "LEFT JOIN data_sources ds ON ds.id = p.source_id WHERE p.id = :id"
            ),
            {"id": port_id},
        )
    ).one_or_none()
    if row is None:
        return None
    weather = (
        await session.execute(
            sa.text(
                "SELECT w.valid_at, w.retrieved_at, w.temperature_c, w.wind_speed_kn, "
                "w.wind_direction_deg, w.wind_gusts_kn, w.precipitation_mm, ds.key AS source, "
                "ds.attribution FROM weather_observations w JOIN data_sources ds "
                "ON ds.id = w.source_id WHERE w.port_id = :id ORDER BY w.valid_at DESC LIMIT 1"
            ),
            {"id": port_id},
        )
    ).one_or_none()
    in_port, _ = await port_calls(
        session, port_id=port_id, open_only=True, limit=100, offset=0, now=now
    )
    recent, _ = await port_calls(session, port_id=port_id, limit=20, offset=0, now=now)
    return dict(row._mapping) | {
        "weather": dict(weather._mapping) if weather else None,
        "in_port": in_port,
        "recent_calls": recent,
    }


async def fleet_summary(session: AsyncSession, now: datetime) -> dict[str, Any]:
    params = {"live": now - timedelta(seconds=LIVE_S), "recent": now - timedelta(seconds=RECENT_S)}
    counts = (
        await session.execute(
            sa.text(
                """
                SELECT count(*) AS total,
                  count(*) FILTER (WHERE last_seen_at >= :live) AS live,
                  count(*) FILTER (WHERE last_seen_at >= :recent) AS recent,
                  count(*) FILTER (WHERE last_seen_at >= :recent AND last_sog_knots > 1
                                   AND COALESCE(last_nav_status, 0) NOT IN (1, 5)) AS underway,
                  count(*) FILTER (WHERE last_seen_at >= :recent AND last_nav_status IN (1, 5))
                    AS anchored
                FROM vessels
                """
            ),
            params,
        )
    ).one()

    async def grouped(sql: str) -> dict[str, int]:
        return {str(r.k): int(r.n) for r in await session.execute(sa.text(sql), params)}

    return {
        "generated_at": now,
        "vessels_total": counts.total,
        "vessels_live": counts.live,
        "vessels_recent": counts.recent,
        "underway": counts.underway,
        "at_anchor_or_moored": counts.anchored,
        "in_port": (
            await session.execute(
                sa.text(
                    "SELECT count(DISTINCT vessel_id) FROM voyage_port_calls "
                    "WHERE actual_departure_at IS NULL"
                )
            )
        ).scalar_one(),
        "by_class": await grouped(
            "SELECT vessel_class AS k, count(*) AS n FROM vessels "
            "WHERE last_seen_at >= :recent GROUP BY 1"
        ),
        "by_data_mode": await grouped(
            "SELECT ds.data_mode AS k, count(*) AS n FROM vessels v "
            "JOIN data_sources ds ON ds.id = v.last_position_source_id "
            "WHERE v.last_seen_at >= :recent GROUP BY 1"
        ),
        "carrier_associations": await grouped(
            "SELECT c.code AS k, count(DISTINCT h.vessel_id) AS n FROM vessel_carrier_history h "
            "JOIN carriers c ON c.id = h.carrier_id WHERE h.valid_to IS NULL GROUP BY 1"
        ),
        "port_calls_last_24h": (
            await session.execute(
                sa.text("SELECT count(*) FROM voyage_port_calls WHERE actual_arrival_at >= :since"),
                {"since": now - timedelta(hours=24)},
            )
        ).scalar_one(),
    }


async def approaching_vessels(
    session: AsyncSession, now: datetime, *, within_nm: float, limit: int
) -> list[dict[str, Any]]:
    rows = await session.execute(
        sa.text(
            f"""
            SELECT {_VESSEL_COLUMNS},
                   np.id AS port_id, np.unlocode, np.name AS port_name, np.country_code,
                   ST_Y(np.location::geometry) AS port_lat, ST_X(np.location::geometry) AS port_lon,
                   np.distance_m
            {_VESSEL_FROM}
            JOIN LATERAL (
                SELECT p.id, p.unlocode, p.name, p.country_code, p.location,
                       ST_Distance(p.location, v.last_location) AS distance_m
                FROM ports p WHERE ST_DWithin(p.location, v.last_location, :radius)
                ORDER BY p.location <-> v.last_location LIMIT 1
            ) np ON true
            WHERE v.last_seen_at >= :recent AND v.last_sog_knots >= 3
              AND NOT EXISTS (SELECT 1 FROM voyage_port_calls c WHERE c.vessel_id = v.id
                              AND c.port_id = np.id AND c.actual_departure_at IS NULL)
            ORDER BY np.distance_m
            LIMIT :candidates
            """
        ),
        {
            "radius": within_nm * 1852,
            "recent": now - timedelta(seconds=RECENT_S),
            "candidates": limit * 4,
        },
    )
    out = []
    for r in rows:
        if is_approaching(
            vessel_lat=r.lat, vessel_lon=r.lon, cog_deg=r.last_cog_deg, sog_knots=r.last_sog_knots,
            port_lat=r.port_lat, port_lon=r.port_lon,
        ):  # fmt: skip
            out.append(
                {
                    "vessel": vessel_summary(r, now),
                    "port": {
                        "id": r.port_id,
                        "unlocode": r.unlocode,
                        "name": r.port_name,
                        "country_code": r.country_code,
                    },
                    "distance_nm": round(r.distance_m / 1852, 1),
                }
            )
        if len(out) >= limit:
            break
    return out


async def list_alerts(
    session: AsyncSession, *, status: str, severity: str | None, limit: int, offset: int
) -> tuple[list[dict[str, Any]], int]:
    clauses: list[str] = []
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if status == "open":
        clauses.append("a.resolved_at IS NULL")
    elif status == "resolved":
        clauses.append("a.resolved_at IS NOT NULL")
    if severity:
        clauses.append("a.severity = :severity")
        params["severity"] = severity
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    total = (
        await session.execute(sa.text(f"SELECT count(*) FROM system_alerts a{where}"), params)
    ).scalar_one()
    rows = await session.execute(
        sa.text(
            "SELECT a.id, a.severity, a.category, a.title, a.message, a.entity_type, a.entity_id, "
            "ds.key AS data_source, a.raised_at, a.acknowledged_at, a.resolved_at "
            f"FROM system_alerts a LEFT JOIN data_sources ds ON ds.id = a.data_source_id{where} "
            "ORDER BY a.raised_at DESC LIMIT :limit OFFSET :offset"
        ),
        params,
    )
    return [dict(r._mapping) for r in rows], total
