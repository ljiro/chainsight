"""Collect current weather for monitored ports from Open-Meteo (Celery task body).

Monitored ports = WEATHER_PORT_UNLOCODES + ports with an AIS-detected call in the last
7 days, capped at WEATHER_MAX_PORTS. Each run's observations are kept (never
overwritten), so they form a timestamped history for later model features.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.engine import Engine

from core.logging_config import get_logger
from core.settings import Settings
from core.timeutils import utcnow
from integrations.weather.open_meteo import OpenMeteoClient, OpenMeteoError
from services.ingestion.sources import (
    mark_error_sync,
    mark_success_sync,
    source_info_sync,
    write_log_sync,
)

log = get_logger(__name__)

SOURCE_KEY = "open-meteo"
CHUNK = 50

_PORTS = sa.text(
    """
    SELECT id, ST_Y(location::geometry) AS lat, ST_X(location::geometry) AS lon FROM ports
    WHERE unlocode = ANY(:configured)
       OR id IN (SELECT port_id FROM voyage_port_calls
                 WHERE COALESCE(actual_arrival_at, created_at) > now() - interval '7 days')
    ORDER BY (unlocode = ANY(:configured)) DESC, unlocode
    LIMIT :limit
    """
)
_INSERT = sa.text(
    """
    INSERT INTO weather_observations (port_id, location, kind, valid_at, temperature_c,
        wind_speed_kn, wind_direction_deg, wind_gusts_kn, precipitation_mm, source_id)
    SELECT r.port_id, ST_SetSRID(ST_MakePoint(r.lon, r.lat), 4326)::geography, 'current',
           r.valid_at, r.temperature_c, r.wind_speed_kn, r.wind_direction_deg, r.wind_gusts_kn,
           r.precipitation_mm, :source_id
    FROM jsonb_to_recordset(CAST(:rows AS jsonb)) AS r(port_id uuid, lon float8, lat float8,
        valid_at timestamptz, temperature_c real, wind_speed_kn real, wind_direction_deg real,
        wind_gusts_kn real, precipitation_mm real)
    ON CONFLICT (port_id, kind, valid_at, source_id) DO NOTHING
    """
)


@dataclass(frozen=True)
class WeatherRunResult:
    ports: int
    inserted: int
    failed: bool


def collect_port_weather(
    engine: Engine, settings: Settings, client: OpenMeteoClient
) -> WeatherRunResult:
    started = utcnow()
    with engine.connect() as conn:
        source_id, _ = source_info_sync(conn, SOURCE_KEY)
        ports = conn.execute(
            _PORTS,
            {"configured": settings.weather_port_unlocodes, "limit": settings.weather_max_ports},
        ).all()
    if not ports:
        log.info("weather_no_monitored_ports")
        return WeatherRunResult(0, 0, False)

    rows: list[dict[str, object]] = []
    try:
        for i in range(0, len(ports), CHUNK):
            chunk = ports[i : i + CHUNK]
            for port, point in zip(
                chunk, client.current([(p.lat, p.lon) for p in chunk]), strict=True
            ):
                rows.append(
                    {"port_id": str(port.id), "lon": port.lon, "lat": port.lat}
                    | point.model_dump(exclude={"latitude", "longitude"}, mode="json")
                )
    except OpenMeteoError as exc:
        # Graceful failure: keep what we have, mark the source, never crash the worker.
        with engine.begin() as conn:
            write_log_sync(
                conn, source_id, started, status="failed", received=len(ports), error=str(exc)
            )
            mark_error_sync(conn, source_id, str(exc))
        log.warning("weather_collection_failed", error=str(exc))
        return WeatherRunResult(len(ports), 0, True)

    with engine.begin() as conn:
        inserted = conn.execute(
            _INSERT, {"rows": json.dumps(rows), "source_id": source_id}
        ).rowcount
        write_log_sync(
            conn,
            source_id,
            started,
            status="succeeded",
            received=len(rows),
            accepted=inserted,
            duplicate=len(rows) - inserted,
            details={"ports": len(ports)},
        )
        mark_success_sync(conn, source_id)
    log.info("weather_collected", ports=len(ports), inserted=inserted)
    return WeatherRunResult(len(ports), inserted, False)
