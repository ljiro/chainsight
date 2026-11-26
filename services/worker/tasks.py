from __future__ import annotations

import asyncio
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


async def _sync_carriers() -> dict[str, str]:
    from database.session import get_async_engine, get_sessionmaker
    from services.shipment_tracking.registry import build_registry, sync_integration_statuses

    try:
        async with get_sessionmaker()() as session, session.begin():
            return await sync_integration_statuses(session, build_registry(get_settings()))
    finally:
        await get_async_engine().dispose()


@shared_task(name="carriers.sync_integrations")  # type: ignore[untyped-decorator]
def sync_carrier_integrations() -> dict[str, str]:
    """Recompute every carrier's tracking integration status (honest, credential-aware)."""
    return asyncio.run(_sync_carriers())


async def _refresh_shipments() -> dict[str, int]:
    import sqlalchemy as sa

    from database.session import get_async_engine, get_sessionmaker
    from integrations.carriers.models import CarrierError, TrackingReference
    from services.shipment_tracking.registry import build_registry
    from services.shipment_tracking.service import track

    s = get_settings()
    registry = build_registry(s)
    refreshed = failed = 0
    try:
        async with get_sessionmaker()() as session:
            due = (
                await session.execute(
                    sa.text(
                        "SELECT c.code, s.reference_type, s.reference_value FROM shipments s "
                        "JOIN carriers c ON c.id = s.carrier_id JOIN data_sources ds ON ds.id = s.source_id "
                        "WHERE ds.key <> 'csv-import' AND s.status NOT IN ('delivered', 'unknown') "
                        "AND (s.last_refreshed_at IS NULL "
                        "OR s.last_refreshed_at < now() - make_interval(mins => :m)) "
                        "ORDER BY s.last_refreshed_at NULLS FIRST LIMIT 200"
                    ),
                    {"m": s.shipment_auto_refresh_minutes},
                )
            ).all()
            for row in due:
                ref = TrackingReference(
                    reference_type=row.reference_type, value=row.reference_value
                )
                try:
                    await track(session, registry, carrier_code=row.code, reference=ref,
                                threshold_hours=s.shipment_delay_threshold_hours)  # fmt: skip
                    await session.commit()
                    refreshed += 1
                except CarrierError:
                    await session.rollback()
                    failed += 1
                except Exception:
                    await session.rollback()
                    failed += 1
                    log.exception("shipment_refresh_failed", carrier=row.code)
    finally:
        await get_async_engine().dispose()
    return {"due": len(due), "refreshed": refreshed, "failed": failed}


@shared_task(name="carriers.refresh_shipments")  # type: ignore[untyped-decorator]
def refresh_shipments() -> dict[str, int]:
    """Re-track active, carrier-backed shipments whose data is older than the refresh interval."""
    return asyncio.run(_refresh_shipments())


@shared_task(name="ml.detect_anomalies")  # type: ignore[untyped-decorator]
def detect_anomalies() -> dict[str, int]:
    """Rule-based anomaly detection for watched vessels (every 5 minutes)."""
    from ml.anomaly_detection.runner import run_anomaly_detection

    with get_sync_engine().begin() as conn:
        return run_anomaly_detection(conn, utcnow()).__dict__


@shared_task(name="ml.predict")  # type: ignore[untyped-decorator]
def ml_predict() -> dict[str, int]:
    """Append new ETA and delay predictions from the production models."""
    from services.ml_jobs import run_predictions

    return run_predictions(get_sync_engine(), utcnow())


@shared_task(name="ml.congestion")  # type: ignore[untyped-decorator]
def ml_congestion() -> dict[str, int]:
    """Hourly port congestion observations + 24 h forecasts."""
    from services.ml_jobs import run_congestion

    return run_congestion(get_sync_engine(), utcnow())


@shared_task(name="ml.evaluate")  # type: ignore[untyped-decorator]
def ml_evaluate() -> dict[str, int]:
    """Back-fill observed outcomes and record live model metrics."""
    from services.ml_jobs import run_evaluation

    return run_evaluation(get_sync_engine(), utcnow())
