"""Fixed-window rate limiting in Redis (one-minute windows).

Fails open: if Redis is unreachable the request proceeds and a warning is logged, so a cache
outage degrades protection rather than taking the API down.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from chainsight_api.health import get_redis
from core.logging_config import get_logger

log = get_logger(__name__)
WINDOW_SECONDS = 60


@dataclass(frozen=True)
class Decision:
    allowed: bool
    limit: int
    remaining: int
    reset_in: int


async def hit(bucket: str, limit: int, *, now: float | None = None) -> Decision:
    now = time.time() if now is None else now
    window = int(now // WINDOW_SECONDS)
    reset_in = WINDOW_SECONDS - int(now % WINDOW_SECONDS)
    key = f"rl:{bucket}:{window}"
    try:
        pipe = get_redis().pipeline(transaction=True)
        pipe.incr(key)
        pipe.expire(key, WINDOW_SECONDS + 5)
        count = int((await pipe.execute())[0])
    except Exception as exc:
        log.warning("rate_limit_unavailable", error_type=type(exc).__name__)
        return Decision(True, limit, limit, reset_in)
    return Decision(count <= limit, limit, max(0, limit - count), reset_in)
