"""Celery application.

Run a worker:  celery -A services.worker.app worker -l INFO -Q default,ingest,ml
Run beat:      celery -A services.worker.app beat -l INFO
"""

from __future__ import annotations

from typing import Any

from celery import Celery
from celery.schedules import crontab
from celery.signals import setup_logging

from core.logging_config import configure_logging
from core.settings import get_settings


@setup_logging.connect  # type: ignore[untyped-decorator]  # celery is untyped
def _configure_worker_logging(**_: Any) -> None:
    # Replaces Celery's logging setup so worker/beat logs match the API's structured format.
    s = get_settings()
    configure_logging(s.log_level, s.log_format)


def create_celery() -> Celery:
    s = get_settings()
    app = Celery("chainsight", broker=s.celery_broker_url, backend=s.celery_result_backend)
    app.conf.update(
        task_default_queue="default",
        task_routes={
            "ingest.*": {"queue": "ingest"},
            "carriers.*": {"queue": "ingest"},
            "ml.*": {"queue": "ml"},
        },
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        task_time_limit=15 * 60,
        task_soft_time_limit=14 * 60,
        timezone="UTC",
        enable_utc=True,
        result_expires=24 * 3600,
        broker_connection_retry_on_startup=True,
        beat_schedule={
            "system-heartbeat": {"task": "system.heartbeat", "schedule": 60.0},
            "data-freshness": {"task": "ingest.check_freshness", "schedule": 60.0},
            "port-weather": {"task": "ingest.weather.ports", "schedule": crontab(minute=5)},
            "carrier-integrations": {"task": "carriers.sync_integrations", "schedule": 600.0},
            "shipment-refresh": {"task": "carriers.refresh_shipments", "schedule": 1800.0},
            "ml-anomalies": {"task": "ml.detect_anomalies", "schedule": 300.0},
            "ml-predict": {"task": "ml.predict", "schedule": 1800.0},
            "ml-congestion": {"task": "ml.congestion", "schedule": crontab(minute=2)},
            "ml-evaluate": {"task": "ml.evaluate", "schedule": crontab(minute=15)},
        },
    )
    app.autodiscover_tasks(["services.worker"], related_name="tasks", force=True)
    return app


celery_app = create_celery()
app = celery_app
