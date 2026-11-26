"""Apply anomaly rules to watched vessels and keep system_alerts in sync.

Watched vessels = vessels with an active carrier association or on an active shipment leg.
One open alert per (vessel, anomaly type); it resolves automatically when the condition
clears. Each run is a point-in-time evaluation as of `now`.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from core.logging_config import get_logger
from ml.anomaly_detection.rules import (
    Anomaly,
    Fix,
    RuleConfig,
    ais_silence,
    long_port_wait,
    route_deviation,
    speed_drop,
    unexpected_stop,
)
from ml.common.destinations import resolve_destination

log = get_logger(__name__)
LOOKBACK = timedelta(hours=6)
FEED_GAP_TOLERANCE = timedelta(minutes=10)
ALL_TYPES = ("unexpected_stop", "speed_drop", "ais_silence", "long_port_wait", "route_deviation")

_WATCHED = sa.text(
    """
    SELECT DISTINCT v.id, v.name, v.destination, v.last_seen_at,
           ST_Y(v.last_location::geometry) AS lat, ST_X(v.last_location::geometry) AS lon,
           v.last_sog_knots, v.last_nav_status, v.last_position_source_id
    FROM vessels v
    WHERE v.last_seen_at > :since AND (
        EXISTS (SELECT 1 FROM vessel_carrier_history h WHERE h.vessel_id = v.id AND h.valid_to IS NULL)
        OR EXISTS (SELECT 1 FROM shipment_vessels sv JOIN shipments s ON s.id = sv.shipment_id
                   WHERE sv.vessel_id = v.id AND s.status NOT IN ('delivered', 'unknown')))
    """
)
_FIXES = sa.text(
    """
    SELECT p.observed_at, ST_Y(p.location::geometry) AS lat, ST_X(p.location::geometry) AS lon,
           p.sog_knots AS sog, p.nav_status,
           EXISTS (SELECT 1 FROM port_areas a WHERE ST_Intersects(a.area, p.location)) AS in_port
    FROM vessel_positions p WHERE p.vessel_id = :vid AND p.observed_at > :since AND p.observed_at <= :now
    ORDER BY p.observed_at
    """
)


@dataclass(frozen=True)
class RunResult:
    vessels: int
    raised: int
    resolved: int


def _port_lookups(
    conn: Connection,
) -> tuple[set[str], dict[str, str], dict[str, tuple[float, float]]]:
    rows = conn.execute(
        sa.text(
            "SELECT unlocode, upper(name) AS name, ST_Y(location::geometry) AS lat, "
            "ST_X(location::geometry) AS lon FROM ports WHERE unlocode IS NOT NULL"
        )
    ).all()
    codes = {r.unlocode for r in rows}
    counts: dict[str, int] = {}
    for r in rows:
        counts[r.name] = counts.get(r.name, 0) + 1
    names = {r.name: r.unlocode for r in rows if counts[r.name] == 1}
    coords = {r.unlocode: (r.lat, r.lon) for r in rows}
    return codes, names, coords


def feed_continuous(
    windows: list[tuple[datetime, datetime]], since: datetime, now: datetime
) -> bool:
    """True if ingestion windows cover [since, now] with no gap over FEED_GAP_TOLERANCE.

    Silence is only the vessel's anomaly if *our* feed was running; otherwise it is ours."""
    relevant = sorted(w for w in windows if w[1] >= since)
    if not relevant or relevant[0][0] > since + FEED_GAP_TOLERANCE:
        return False
    cursor = relevant[0][1]
    for start, end in relevant[1:]:
        if start - cursor > FEED_GAP_TOLERANCE:
            return False
        cursor = max(cursor, end)
    return now - cursor <= FEED_GAP_TOLERANCE


def _feed_windows(conn: Connection, now: datetime) -> dict[Any, list[tuple[datetime, datetime]]]:
    rows = conn.execute(
        sa.text(
            "SELECT data_source_id, started_at, finished_at FROM ingestion_logs "
            "WHERE finished_at IS NOT NULL AND finished_at > :since AND records_accepted > 0"
        ),
        {"since": now - timedelta(hours=50)},
    )
    out: dict[Any, list[tuple[datetime, datetime]]] = {}
    for r in rows:
        out.setdefault(r.data_source_id, []).append((r.started_at, r.finished_at))
    return out


def detect_for_vessel(
    conn: Connection,
    vessel: Any,
    now: datetime,
    cfg: RuleConfig,
    lookups: tuple[Any, Any, Any],
    feeds: dict[Any, list[tuple[datetime, datetime]]],
) -> list[Anomaly]:
    codes, names, coords = lookups
    rows = conn.execute(_FIXES, {"vid": vessel.id, "since": now - LOOKBACK, "now": now})
    fixes = [Fix(r.observed_at, r.lat, r.lon, r.sog, r.nav_status, r.in_port) for r in rows]
    found: list[Anomaly | None] = [unexpected_stop(fixes, cfg), speed_drop(fixes, cfg)]
    if fixes:
        silence = ais_silence(fixes[-1], now, cfg)
        windows = feeds.get(vessel.last_position_source_id, [])
        if silence is not None and feed_continuous(windows, fixes[-1].t, now):
            found.append(silence)
    call = conn.execute(
        sa.text(
            "SELECT c.port_id, c.actual_arrival_at FROM voyage_port_calls c WHERE c.vessel_id = :vid "
            "AND c.actual_departure_at IS NULL ORDER BY c.created_at DESC LIMIT 1"
        ),
        {"vid": vessel.id},
    ).one_or_none()
    if call is not None:
        durations = [
            r.h
            for r in conn.execute(
                sa.text(
                    "SELECT EXTRACT(EPOCH FROM actual_departure_at - actual_arrival_at) / 3600 AS h "
                    "FROM voyage_port_calls WHERE port_id = :p AND actual_arrival_at IS NOT NULL "
                    "AND actual_departure_at IS NOT NULL ORDER BY actual_departure_at DESC LIMIT 200"
                ),
                {"p": call.port_id},
            )
        ]
        median = float(statistics.median(durations)) if len(durations) >= 10 else None
        found.append(long_port_wait(call.actual_arrival_at, now, median, cfg))
    code, _ = resolve_destination(vessel.destination, codes, names)
    found.append(route_deviation(fixes, coords.get(code) if code else None, cfg))
    return [a for a in found if a is not None]


def run_anomaly_detection(
    conn: Connection, now: datetime, cfg: RuleConfig | None = None
) -> RunResult:
    cfg = cfg or RuleConfig()
    lookups = _port_lookups(conn)
    vessels = conn.execute(_WATCHED, {"since": now - timedelta(hours=48)}).all()
    feeds = _feed_windows(conn, now)
    raised = resolved = 0
    for v in vessels:
        anomalies = {a.type: a for a in detect_for_vessel(conn, v, now, cfg, lookups, feeds)}
        for a in anomalies.values():
            raised += conn.execute(
                sa.text(
                    "INSERT INTO system_alerts (severity, category, title, message, entity_type, "
                    "entity_id, dedupe_key, details, raised_at) VALUES (:sev, 'vessel_anomaly', :title, "
                    ":msg, 'vessel', :vid, :key, CAST(:details AS jsonb), :now) "
                    "ON CONFLICT (dedupe_key) WHERE resolved_at IS NULL DO NOTHING"
                ),
                {
                    "sev": a.severity,
                    "title": f"{a.title}: {v.name or 'unnamed vessel'}",
                    "msg": a.message,
                    "vid": v.id,
                    "key": f"anomaly:{a.type}:{v.id}",
                    "now": now,
                    "details": json.dumps(
                        {
                            "type": a.type,
                            "started_at": a.started_at.isoformat(),
                            "evidence": a.evidence,
                        },
                        default=str,
                    ),
                },
            ).rowcount
        cleared = [t for t in ALL_TYPES if t not in anomalies]
        resolved += conn.execute(
            sa.text(
                "UPDATE system_alerts SET resolved_at = :now WHERE resolved_at IS NULL "
                "AND dedupe_key = ANY(:keys)"
            ),
            {"now": now, "keys": [f"anomaly:{t}:{v.id}" for t in cleared]},
        ).rowcount
    log.info("anomaly_detection_run", vessels=len(vessels), raised=raised, resolved=resolved)
    return RunResult(len(vessels), raised, resolved)
