from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import sqlalchemy as sa
from sqlalchemy.engine import Engine

from core.settings import Settings
from integrations.ports.unlocode import iter_seaports
from integrations.weather.open_meteo import OpenMeteoClient
from services.alerting.freshness import check_freshness
from services.ingestion.port_registry import import_ports
from services.ingestion.weather import collect_port_weather

CSV = """Change,Country,Location,Name,NameWoDiacritics,Subdivision,Status,Function,Date,IATA,Coordinates,Remarks
,ZY,WXA,Weather Port,Weather Port,,AI,1-------,2401,,5000N 00100W,
"""


def q(engine: Engine, sql: str, **params: Any) -> list[Any]:
    with engine.connect() as conn:
        return list(conn.execute(sa.text(sql), params))


def set_source(engine: Engine, key: str, status: str, last_success: datetime | None) -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.text("UPDATE data_sources SET status = :s, last_success_at = :t WHERE key = :k"),
            {"s": status, "t": last_success, "k": key},
        )


def test_freshness_raises_once_then_resolves(migrated_db: Engine) -> None:
    now = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
    set_source(migrated_db, "aisstream", "active", now - timedelta(minutes=10))  # 60 s interval
    set_source(migrated_db, "imf-portwatch", "not_configured", None)  # never monitored

    with migrated_db.begin() as conn:
        first = check_freshness(conn, now)
        second = check_freshness(conn, now + timedelta(minutes=1))
    assert ("aisstream", "raised") in [(c.source_key, c.change) for c in first]
    assert "aisstream" not in [c.source_key for c in second]  # deduplicated
    assert not [c for c in first if c.source_key == "imf-portwatch"]

    alerts = q(
        migrated_db,
        "SELECT title, severity, resolved_at FROM system_alerts WHERE dedupe_key = 'freshness:aisstream'",
    )
    assert [(a.title, a.severity, a.resolved_at) for a in alerts] == [
        ("AISStream data is stale", "warning", None)
    ]
    assert (
        q(migrated_db, "SELECT status FROM data_sources WHERE key = 'aisstream'")[0].status
        == "degraded"
    )

    set_source(migrated_db, "aisstream", "active", now + timedelta(minutes=2))
    with migrated_db.begin() as conn:
        changes = check_freshness(conn, now + timedelta(minutes=2, seconds=5))
    assert ("aisstream", "resolved") in [(c.source_key, c.change) for c in changes]
    assert (
        q(
            migrated_db,
            "SELECT resolved_at FROM system_alerts WHERE dedupe_key = 'freshness:aisstream'",
        )[0].resolved_at
        is not None
    )
    set_source(migrated_db, "aisstream", "not_configured", None)


def _weather_client(status: int = 200) -> OpenMeteoClient:
    def handle(req: httpx.Request) -> httpx.Response:
        if status != 200:
            return httpx.Response(status, json={"error": True, "reason": "bad"})
        n = len(req.url.params["latitude"].split(","))
        item = {
            "latitude": 50.0,
            "longitude": -1.0,
            "current": {"time": "2026-11-26T04:00", "temperature_2m": 8.0, "wind_speed_10m": 18.0,
                        "wind_direction_10m": 200, "wind_gusts_10m": 30.0, "precipitation": 1.2},
        }  # fmt: skip
        return httpx.Response(200, json=[item] * n if n > 1 else item)

    return OpenMeteoClient(
        client=httpx.Client(transport=httpx.MockTransport(handle)), sleep=lambda _: None
    )


def test_weather_collection_for_configured_ports(migrated_db: Engine) -> None:
    import_ports(migrated_db, iter_seaports(io.StringIO(CSV)), radius_m=5000)
    settings = Settings(_env_file=None, weather_port_unlocodes=["ZYWXA"])  # type: ignore[call-arg]

    result = collect_port_weather(migrated_db, settings, _weather_client())
    assert result.failed is False and result.ports >= 1 and result.inserted >= 1
    again = collect_port_weather(migrated_db, settings, _weather_client())
    assert again.inserted == 0  # same valid_at: history is kept, not duplicated or overwritten

    obs = q(
        migrated_db,
        "SELECT w.wind_speed_kn, w.kind, w.valid_at FROM weather_observations w JOIN ports p ON p.id = w.port_id WHERE p.unlocode = 'ZYWXA'",
    )
    assert [(o.wind_speed_kn, o.kind) for o in obs] == [(18.0, "current")]
    src = q(
        migrated_db, "SELECT status, last_success_at FROM data_sources WHERE key = 'open-meteo'"
    )[0]
    assert src.status == "active" and src.last_success_at is not None


def test_weather_failure_is_graceful_and_recorded(migrated_db: Engine) -> None:
    import_ports(migrated_db, iter_seaports(io.StringIO(CSV)), radius_m=5000)
    settings = Settings(_env_file=None, weather_port_unlocodes=["ZYWXA"])  # type: ignore[call-arg]
    result = collect_port_weather(migrated_db, settings, _weather_client(status=400))
    assert result.failed is True
    src = q(
        migrated_db, "SELECT status, last_error_message FROM data_sources WHERE key = 'open-meteo'"
    )[0]
    assert src.status == "error" and "HTTP 400" in src.last_error_message
    log = q(
        migrated_db,
        "SELECT l.status FROM ingestion_logs l JOIN data_sources d ON d.id = l.data_source_id WHERE d.key = 'open-meteo' ORDER BY l.started_at DESC LIMIT 1",
    )[0]
    assert log.status == "failed"
