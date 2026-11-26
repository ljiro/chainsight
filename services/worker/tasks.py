from __future__ import annotations

import socket
from typing import Any

from celery import shared_task

from core.logging_config import get_logger
from core.settings import get_settings
from core.timeutils import utcnow
from database.session import get_sync_engine
from integrations.weather.open_meteo import OpenMeteoClient
from services.alerting.freshness import check_freshness
from services.ingestion.weather import collect_port_weather

log = get_logger(__name__)


@shared_task(name="system.heartbeat")  # type: ignore[untyped-decorator]  # celery is untyped
def heartbeat() -> dict[str, Any]:
    """Proves the broker → worker path works. Scheduled by beat every minute."""
    payload = {"status": "ok", "host": socket.gethostname(), "at": utcnow().isoformat()}
    log.info("worker_heartbeat", **payload)
    return payload


@shared_task(name="ingest.check_freshness")  # type: ignore[untyped-decorator]
def freshness() -> dict[str, Any]:
    """Degrade stale data sources and raise/resolve freshness alerts."""
    with get_sync_engine().begin() as conn:
        changes = check_freshness(conn, utcnow())
    return {"changes": [c.__dict__ for c in changes]}


@shared_task(name="ingest.weather.ports")  # type: ignore[untyped-decorator]
def port_weather() -> dict[str, Any]:
    """Collect current Open-Meteo conditions for monitored ports (hourly)."""
    s = get_settings()
    key = s.open_meteo_api_key.get_secret_value() if s.open_meteo_api_key else None
    result = collect_port_weather(
        get_sync_engine(), s, OpenMeteoClient(s.open_meteo_base_url, api_key=key)
    )
    return result.__dict__
