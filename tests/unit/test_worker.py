from __future__ import annotations

from services.worker.app import celery_app
from services.worker.tasks import heartbeat


def test_heartbeat_task_registered_and_scheduled() -> None:
    celery_app.loader.import_default_modules()
    assert "system.heartbeat" in celery_app.tasks
    assert celery_app.conf.beat_schedule["system-heartbeat"]["task"] == "system.heartbeat"


def test_heartbeat_runs_eagerly() -> None:
    result = heartbeat.apply().get()
    assert result["status"] == "ok"
    assert result["at"].endswith("+00:00")
