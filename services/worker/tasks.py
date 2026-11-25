from __future__ import annotations

import socket
from typing import Any

from celery import shared_task

from core.logging_config import get_logger
from core.timeutils import utcnow

log = get_logger(__name__)


@shared_task(name="system.heartbeat")  # type: ignore[untyped-decorator]  # celery is untyped
def heartbeat() -> dict[str, Any]:
    """Proves the broker → worker path works. Scheduled by beat every minute."""
    payload = {"status": "ok", "host": socket.gethostname(), "at": utcnow().isoformat()}
    log.info("worker_heartbeat", **payload)
    return payload
