"""Prometheus metrics for the API, served at `/metrics` (outside `/api/v1`, so the web
app's proxy never exposes it; protect it with METRICS_TOKEN outside a private network).

Labels use the *route template* (`/api/v1/shipments/{shipment_id}`), never the raw path, so
cardinality stays bounded.
"""

from __future__ import annotations

import hmac
import time
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    platform_collector,
    process_collector,
)

from core.settings import Settings

REGISTRY = CollectorRegistry(auto_describe=True)
process_collector.ProcessCollector(registry=REGISTRY)
platform_collector.PlatformCollector(registry=REGISTRY)

HTTP_REQUESTS = Counter(
    "chainsight_http_requests_total", "HTTP requests", ["method", "route", "status"],
    registry=REGISTRY,
)  # fmt: skip
HTTP_LATENCY = Histogram(
    "chainsight_http_request_duration_seconds", "HTTP request latency", ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30), registry=REGISTRY,
)  # fmt: skip
HTTP_IN_FLIGHT = Gauge(
    "chainsight_http_requests_in_flight", "Requests in progress", registry=REGISTRY
)
AUTH_EVENTS = Counter(
    "chainsight_auth_events_total", "Authentication outcomes", ["event"], registry=REGISTRY
)
RATE_LIMITED = Counter(
    "chainsight_rate_limited_total",
    "Requests refused by rate limiting",
    ["scope"],
    registry=REGISTRY,
)
WEBHOOK_DELIVERIES = Counter(
    "chainsight_webhook_deliveries_total", "Carrier webhook deliveries", ["carrier", "outcome"],
    registry=REGISTRY,
)  # fmt: skip
APP_INFO = Gauge("chainsight_app_info", "Build information", ["version", "env"], registry=REGISTRY)


def _route(request: Request) -> str:
    """The full route template, e.g. /api/v1/shipments/{shipment_id}; unmatched paths collapse
    to one label. Built from the real path and its path parameters, because nested routers
    expose only the leaf route's (prefix-less) template."""
    if request.scope.get("route") is None:
        return "unmatched"
    params = {str(v): k for k, v in (request.scope.get("path_params") or {}).items()}
    return "/".join(f"{{{params[s]}}}" if s in params else s for s in request.url.path.split("/"))


def install_metrics(app: FastAPI, settings: Settings) -> None:
    if not settings.metrics_enabled:
        return
    APP_INFO.labels(settings.app_version, settings.app_env.value).set(1)

    @app.middleware("http")
    async def record(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.url.path == "/metrics":
            return await call_next(request)
        start = time.perf_counter()
        HTTP_IN_FLIGHT.inc()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            HTTP_IN_FLIGHT.dec()
            route = _route(request)
            HTTP_REQUESTS.labels(request.method, route, str(status)).inc()
            HTTP_LATENCY.labels(request.method, route).observe(time.perf_counter() - start)

    token = settings.metrics_token.get_secret_value() if settings.metrics_token else None

    @app.get("/metrics", include_in_schema=False)
    async def metrics(request: Request) -> Response:
        if token is not None:
            given = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
            if not hmac.compare_digest(given, token):
                return Response(status_code=401, headers={"WWW-Authenticate": "Bearer"})
        return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)
