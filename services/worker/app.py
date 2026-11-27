"""Celery application.

Run a worker:  celery -A services.worker.app worker -l INFO -Q default,ingest,ml
Run beat:      celery -A services.worker.app beat -l INFO
"""

from __future__ import annotations

from typing import Any

from celery import Celery
from celery.schedules import crontab
from celery.signals import (
    setup_logging,
    task_postrun,
    task_prerun,
    worker_init,
    worker_process_init,
)

from core.logging_config import configure_logging
from core.settings import get_settings
from services import observability


@setup_logging.connect  # type: ignore[untyped-decorator]  # celery is untyped
def _configure_worker_logging(**_: Any) -> None:
    # Replaces Celery's logging setup so worker/beat logs match the API's structured format.
    s = get_settings()
    configure_logging(s.log_level, s.log_format)


@worker_init.connect  # type: ignore[untyped-decorator]
def _serve_metrics(**_: Any) -> None:
    observability.serve_worker_metrics(get_settings())


@worker_process_init.connect  # type: ignore[untyped-decorator]
def _init_tracing(**_: Any) -> None:
    # Per child process: exporters and their threads do not survive fork.
    if observability.configure_tracing(get_settings(), "chainsight-worker"):
        from opentelemetry.instrumentation.celery import CeleryInstrumentor

        CeleryInstrumentor().instrument()  # type: ignore[no-untyped-call]


@task_prerun.connect  # type: ignore[untyped-decorator]
def _task_prerun(task_id: str, **_: Any) -> None:
    observability.task_started(task_id)


@task_postrun.connect  # type: ignore[untyped-decorator]
def _task_postrun(task_id: str, task: Any, state: str | None = None, **_: Any) -> None:
    # Fires for successes and failures alike (state SUCCESS / FAILURE / RETRY).
    observability.task_finished(task_id, task.name, state or "UNKNOWN")


def create_celery() -> Celery:
    s = get_settings()
    app = Celery("chainsight", broker=s.celery_broker_url, backend=s.celery_result_backend)
    app.conf.update(
        task_default_queue="default",
        task_routes={
            "ingest.*": {"queue": "ingest"},
            "carriers.*": {"queue": "ingest"},
            "ml.*": {"queue": "ml"},
            "research.*": {"queue": "ml"},
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
            # PortWatch publishes on Tuesdays around 11:00 UTC; FRED daily.
            "research-refresh": {
                "task": "research.refresh",
                "schedule": crontab(hour=13, minute=30),
            },
        },
    )
    app.autodiscover_tasks(["services.worker"], related_name="tasks", force=True)
    return app


celery_app = create_celery()
app = celery_app
