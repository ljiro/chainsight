from __future__ import annotations

import re
import time
import uuid
from collections.abc import Awaitable, Callable

import structlog
from fastapi import FastAPI, Request, Response

from chainsight_api import ratelimit
from chainsight_api.auth import client_ip, settings_of
from chainsight_api.errors import REQUEST_ID_HEADER, error_response
from chainsight_api.metrics import RATE_LIMITED
from core.logging_config import get_logger

log = get_logger("chainsight_api.access")

# Accept caller-supplied IDs only if they look sane (prevents log injection).
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def install_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def ip_rate_limit(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Registered first, so it runs inside request_context (request IDs are already set).
        settings = settings_of(request)
        path = request.url.path
        if (
            settings.rate_limit_enabled
            and path.startswith("/api/v1/")
            and not path.startswith("/api/v1/health")
        ):
            d = await ratelimit.hit(f"ip:{client_ip(request)}", settings.rate_limit_per_minute)
            if not d.allowed:
                RATE_LIMITED.labels("ip").inc()
                response = error_response(
                    request, 429, "rate_limited", "Too many requests; slow down",
                    {"limit": d.limit, "retry_after_seconds": d.reset_in},
                )  # fmt: skip
                response.headers["Retry-After"] = str(d.reset_in)
                return response
        return await call_next(request)

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER, "")
        request_id = incoming if _VALID_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        request.state.request_id = request_id
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        response.headers[REQUEST_ID_HEADER] = request_id
        log.info(
            "request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=duration_ms,
        )
        return response
