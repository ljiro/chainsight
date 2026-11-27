# Observability

## Logs

Structured logs (structlog). `LOG_FORMAT=json` in production. Every API request logs
`request_id` (also returned as `X-Request-ID` and inside error envelopes), method, path,
status and duration. Background jobs log one line per ingestion window, task or model run.

## Metrics (Prometheus)

| Process | Endpoint | Notes |
|---|---|---|
| API | `GET /metrics` on :8000 (not under `/api/v1`, so never proxied) | Bearer `METRICS_TOKEN` if set; multiprocess-aware |
| Celery worker | :9808 | Aggregated from the prefork children (`PROMETHEUS_MULTIPROC_DIR`) |
| AIS consumer | :9809 | Updated once per 60 s ingestion window |

| Metric | Labels | Meaning |
|---|---|---|
| `chainsight_http_requests_total` | method, route, status | Route **templates** (`/api/v1/shipments/{shipment_id}`), so cardinality stays bounded |
| `chainsight_http_request_duration_seconds` | method, route | Latency histogram |
| `chainsight_http_requests_in_flight` | | Concurrent requests |
| `chainsight_auth_events_total` | event | `login`, `login_failed`, `refresh_failed`, `token_expired`, `invalid_token` |
| `chainsight_rate_limited_total` | scope | `ip`, `principal`, `login` |
| `chainsight_webhook_deliveries_total` | carrier, outcome | `processed`, `duplicate`, `signature_invalid`, ... |
| `chainsight_celery_tasks_total` | task, state | `SUCCESS`, `FAILURE`, `RETRY` |
| `chainsight_celery_task_duration_seconds` | task | Runtime histogram |
| `chainsight_ais_messages_total` | source, outcome | `received`, `accepted`, `duplicate` |
| `chainsight_ais_rejected_total` | source, reason | Parser and quality rejections |
| `chainsight_ais_last_window_timestamp_seconds` | source | For the "ingestion stalled" alert |
| `chainsight_app_info` | version, env | Build info |

## Local monitoring stack

```bash
docker compose --profile monitoring up -d
# Prometheus http://localhost:9090   Grafana http://localhost:3001 (admin / GRAFANA_ADMIN_PASSWORD)
```

Grafana provisions the Prometheus datasource and the **ChainSight overview** dashboard:
- API traffic, error ratio, p95 latency by route;
- auth events, rate limiting and webhooks;
- Celery tasks and durations;
- AIS throughput, rejection reasons and ingestion lag.

Both services bind to 127.0.0.1 only.

Alert rules (`infrastructure/monitoring/alerts.yml`, visible under Prometheus → Alerts):

| Alert | Fires when |
|---|---|
| Target down | API, worker or AIS consumer unreachable for 2 min |
| API error rate | More than 5% 5xx for 10 min |
| API slow | p95 above 2 s for 15 min (research excluded) |
| Login failures | More than 50 failed sign-ins in 15 min |
| AIS ingestion stalled | No ingestion window for 10 min |
| Celery failures | A task fails more than 3 times in an hour |

Routing alerts to people (Alertmanager, PagerDuty and so on) is deployment-specific and not
included.

Data freshness also has its own in-app path: the beat task `ingest.check_freshness` raises
deduplicated `system_alerts`, which the dashboard shows.

## Tracing (OpenTelemetry)

Set `OTEL_EXPORTER_OTLP_ENDPOINT` (OTLP over HTTP, for example `http://otel-collector:4318`)
and every process exports traces. Each process sets its own service name:
`chainsight-api`, `chainsight-worker` and `chainsight-ais-consumer`.

What gets instrumented:
- FastAPI (health and metrics excluded);
- SQLAlchemy, Redis and httpx, so carrier and provider calls appear as client spans;
- Celery tasks (one trace per task run). Tasks are scheduled by beat rather than enqueued by
  API requests, so there are no API → worker traces to join.

When the variable is unset, nothing is installed and there is no overhead.
