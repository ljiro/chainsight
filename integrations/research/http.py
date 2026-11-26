"""Small retrying GET helper shared by the research data clients."""

from __future__ import annotations

import time
from collections.abc import Callable

import httpx

from core.logging_config import get_logger

log = get_logger(__name__)
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
USER_AGENT = "ChainSight research collector (open source)"


class ResearchSourceError(Exception):
    pass


def get_with_retries(
    client: httpx.Client,
    url: str,
    params: dict[str, str],
    *,
    retries: int = 4,
    sleep: Callable[[float], None] = time.sleep,
    redact: tuple[str, ...] = (),
) -> httpx.Response:
    """GET with exponential backoff on transport errors and retryable statuses. Parameter
    names in `redact` (API keys) are never logged or put in error messages."""
    delay = 2.0
    for attempt in range(1, retries + 1):
        try:
            resp = client.get(url, params=params, headers={"User-Agent": USER_AGENT})
        except httpx.TransportError as exc:
            if attempt == retries:
                raise ResearchSourceError(f"transport error: {type(exc).__name__}") from exc
        else:
            if resp.status_code == 200:
                return resp
            if resp.status_code not in RETRY_STATUSES or attempt == retries:
                raise ResearchSourceError(f"HTTP {resp.status_code} from {httpx.URL(url).host}")
            retry_after = resp.headers.get("Retry-After")
            if retry_after and retry_after.isdigit():
                delay = float(retry_after)
        safe = {k: ("***" if k in redact else v) for k, v in params.items()}
        log.warning("research_source_retry", url=url, attempt=attempt, delay_s=delay, params=safe)
        sleep(delay)
        delay *= 2
    raise ResearchSourceError("unreachable")  # pragma: no cover
