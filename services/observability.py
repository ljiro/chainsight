"""Prometheus metrics for background processes (Celery worker, AIS consumer) and optional
OpenTelemetry tracing for every process.

- AIS consumer: an in-process registry served on AIS_METRICS_PORT (default 9809).
- Celery worker: the prefork pool runs tasks in child processes, so metrics use
  prometheus_client's multiprocess mode (PROMETHEUS_MULTIPROC_DIR) and the worker's main
  process serves the aggregate on WORKER_METRICS_PORT (default 9808).
- Tracing: enabled only when OTEL_EXPORTER_OTLP_ENDPOINT is set (OTLP over HTTP).
"""

from __future__ import annotations

import os
import time
from typing import Any

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, start_http_server

from core.logging_config import get_logger
from core.settings import Settings

log = get_logger(__name__)

# --- AIS consumer ----------------------------------------------------------------------

AIS_REGISTRY = CollectorRegistry(auto_describe=True)
AIS_MESSAGES = Counter(
    "chainsight_ais_messages_total", "AIS messages by outcome", ["source", "outcome"],
    registry=AIS_REGISTRY,
)  # fmt: skip
AIS_REJECTED = Counter(
    "chainsight_ais_rejected_total", "Rejected AIS messages by reason", ["source", "reason"],
    registry=AIS_REGISTRY,
)  # fmt: skip
AIS_FLUSH_SECONDS = Counter(
    "chainsight_ais_flush_seconds_total", "Time spent writing batches", ["source"],
    registry=AIS_REGISTRY,
)  # fmt: skip
AIS_QUEUE_MAX = Gauge(
    "chainsight_ais_queue_depth_max", "Max queue depth in the last window", ["source"],
    registry=AIS_REGISTRY,
)  # fmt: skip
AIS_LAST_WINDOW = Gauge(
    "chainsight_ais_last_window_timestamp_seconds", "End of the last ingestion window",
    ["source"], registry=AIS_REGISTRY,
)  # fmt: skip


def observe_ais_window(source: str, w: Any) -> None:
    AIS_MESSAGES.labels(source, "received").inc(w.received)
    AIS_MESSAGES.labels(source, "accepted").inc(w.accepted)
    AIS_MESSAGES.labels(source, "duplicate").inc(w.duplicates)
    for reason, n in w.rejected.items():
        AIS_REJECTED.labels(source, reason).inc(n)
    AIS_FLUSH_SECONDS.labels(source).inc(w.flush_seconds)
    AIS_QUEUE_MAX.labels(source).set(w.max_queue_depth)
    AIS_LAST_WINDOW.labels(source).set(time.time())


def serve_ais_metrics(settings: Settings) -> None:
    if not settings.metrics_enabled:
        return
    port = int(os.environ.get("AIS_METRICS_PORT", "9809"))
    start_http_server(port, registry=AIS_REGISTRY)
    log.info("metrics_listening", port=port, process="ais-consumer")


# --- Celery worker ---------------------------------------------------------------------
# Created lazily: in multiprocess mode metric objects must be created after the env var is set.

_task_metrics: dict[str, Any] = {}
_task_started: dict[str, float] = {}


def _metrics() -> dict[str, Any]:
    if not _task_metrics:
        _task_metrics["total"] = Counter(
            "chainsight_celery_tasks_total", "Celery tasks by final state", ["task", "state"]
        )
        _task_metrics["seconds"] = Histogram(
            "chainsight_celery_task_duration_seconds", "Celery task runtime", ["task"],
            buckets=(0.1, 0.5, 1, 5, 15, 60, 180, 600, 900),
        )  # fmt: skip
    return _task_metrics


def task_started(task_id: str) -> None:
    _task_started[task_id] = time.perf_counter()


def task_finished(task_id: str, task_name: str, state: str) -> None:
    m = _metrics()
    m["total"].labels(task_name, state).inc()
    start = _task_started.pop(task_id, None)
    if start is not None:
        m["seconds"].labels(task_name).observe(time.perf_counter() - start)


def serve_worker_metrics(settings: Settings) -> None:
    directory = os.environ.get("PROMETHEUS_MULTIPROC_DIR")
    if not settings.metrics_enabled or not directory:
        return
    from prometheus_client import multiprocess

    os.makedirs(directory, exist_ok=True)
    for f in os.listdir(directory):  # stale files from a previous run would double-count
        os.remove(os.path.join(directory, f))
    registry = CollectorRegistry()
    multiprocess.MultiProcessCollector(registry)  # type: ignore[no-untyped-call]
    port = int(os.environ.get("WORKER_METRICS_PORT", "9808"))
    start_http_server(port, registry=registry)
    log.info("metrics_listening", port=port, process="celery-worker")


# --- OpenTelemetry ---------------------------------------------------------------------


def configure_tracing(settings: Settings, service_name: str | None = None) -> bool:
    """Install an OTLP/HTTP tracer provider and auto-instrument common libraries."""
    endpoint = settings.otel_exporter_otlp_endpoint
    if not endpoint:
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.instrumentation.redis import RedisInstrumentor
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        log.warning("otel_unavailable", hint="install the opentelemetry packages")
        return False
    resource = Resource.create({
        "service.name": service_name or settings.otel_service_name,
        "service.version": settings.app_version,
        "deployment.environment": settings.app_env.value,
    })  # fmt: skip
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=f"{endpoint.rstrip('/')}/v1/traces")
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    SQLAlchemyInstrumentor().instrument(enable_commenter=False)
    HTTPXClientInstrumentor().instrument()
    RedisInstrumentor().instrument()
    log.info("otel_tracing_enabled", endpoint=endpoint, service=service_name)
    return True
