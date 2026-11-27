# ChainSight — System Design

Status: Draft v1 (Phase 0) · Last updated: 2026-11-25

## 1. Purpose and scope

ChainSight is an open-source maritime intelligence and quantitative analytics platform with four modules:

| Module | Question it answers | Primary data |
|---|---|---|
| A. Shipment Intelligence | Where is my shipment in its lifecycle, and will it be late? | Carrier track & trace APIs, CSV imports |
| B. Fleet Intelligence | Where are a carrier's vessels, and what are they doing? | AIS (AISStream), port registry |
| C. Predictive Intelligence | When will this vessel arrive? How likely is this shipment to be late? Is this vessel behaving abnormally? | Historical AIS, shipment events, weather, port activity |
| D. Quantitative Research | Do maritime activity indicators carry information about markets or trade? | Aggregated maritime features, public market/trade data |

Non-goals: automated trading, scraping carrier websites, claiming container-level location from AIS.

## 2. Guiding principles

1. **Honest data.** Every record carries provenance (source, source timestamp, ingestion timestamp) and a *data mode*: `real_time`, `periodic`, `historical`, or `synthetic`. The UI always shows the mode and freshness. If no data exists, the UI says so instead of showing placeholders that look real.
2. **Fleet visibility ≠ container visibility.** AIS tells us where a *vessel* is. A shipment is linked to a vessel only through carrier-provided voyage/vessel assignments (`shipment_vessels`). The UI shows "carried by vessel X according to carrier Y as of T", never "container is at lat/lon".
3. **Append-only history for anything time-varying.** Planned, estimated, and actual milestones live side by side. Predictions are inserted, never updated (except for back-filling the realized outcome).
4. **Replaceable integrations.** Every external provider sits behind an adapter interface. Business logic depends on the internal normalized schema, never on provider payloads.
5. **Modular monolith first.** One deployable backend with strict internal package boundaries. Split only when load or ownership requires it.
6. **Point-in-time correctness.** ML and research code use data *as it was available at the time* (`available_at`/`ingested_at`), not only event time.

## 3. Logical architecture

```text
 External providers ─ AISStream (WS) · Carrier APIs (REST) · Open-Meteo · IMF PortWatch · UN Comtrade · CSV
        │
        ▼
 integrations/          Provider adapters: connect, authenticate, rate-limit, retry, parse,
                        map to internal DTOs. No DB access. Pure + testable with fixtures.
        │
        ▼
 services/              Ingestion runners (Celery tasks, AIS consumer process), normalization,
                        entity resolution, dedup, data-quality checks, persistence, alerting,
                        domain services (shipment tracking, fleet tracking, vessel registry).
        │
        ▼
 database/              SQLAlchemy models + Alembic migrations.
                        PostgreSQL 16 + PostGIS + TimescaleDB.
        │
        ├──────────────► ml/   Feature pipelines, training (offline), inference, evaluation,
        │                      quant_research (features, statistics, backtesting). MLflow tracking.
        ▼
 apps/api/              FastAPI: versioned REST (/api/v1), WebSocket live feed, authN/Z,
                        request validation, pagination, error envelope, OpenAPI.
        │
        ▼
 apps/web/              Next.js dashboard. Talks only to the ChainSight API (never to providers).
```

### 3.1 Package dependency rules

Enforced by `tests/unit/test_architecture.py`:

| Package | May import |
|---|---|
| `core` | (nothing internal) |
| `database` | `core` |
| `integrations` | `core` |
| `ml` | `core`, `database` |
| `services` | `core`, `database`, `integrations`, `ml` |
| `chainsight_api` | `core`, `database`, `services`, `ml` |

Nothing imports `chainsight_api`. Integrations never touch the database; they return validated DTOs that services persist. This keeps adapters unit-testable against recorded fixtures and lets us swap providers.

### 3.2 Runtime processes

| Process | Responsibility | Scaling note |
|---|---|---|
| `api` | FastAPI (uvicorn). REST + WebSocket. | Stateless; scale horizontally. WebSocket fan-out via Redis pub/sub. |
| `worker` | Celery workers: periodic collectors (weather, PortWatch, carrier polling), feature builds, batch inference. | Queue per workload class (`ingest`, `ml`, `default`). |
| `beat` | Celery beat scheduler. | Exactly one instance. |
| `ais-consumer` (Phase 2) | Long-lived WebSocket client to AISStream. Batches positions to DB and publishes to Redis. | One per subscription; not a Celery task, because it is a persistent connection. |
| `web` | Next.js server. Proxies `/api/v1/*` to `api`. | Stateless. |
| `db` | PostgreSQL + PostGIS + TimescaleDB. | Vertical first; read replica for research later. |
| `redis` | Celery broker/result backend, pub/sub for live positions, rate-limit counters, short-lived caches. | Single instance is enough initially. |

### 3.3 Real-time path (Phase 2)

```text
AISStream WS ─► ais-consumer ─► parse + validate ─► dedupe (Redis SETNX key mmsi:ts, TTL 10m)
                                     │
                                     ├─► batch INSERT vessel_positions (hypertable), every 1–2 s or 500 rows
                                     └─► PUBLISH positions:<bbox> (Redis)
                                                   │
API WebSocket /api/v1/ws/positions ◄───────────────┘ ─► browser (MapLibre)
```

We do not introduce Kafka. One AISStream connection over a few bounding boxes produces on the order of hundreds of messages per second at most, which Postgres batch inserts and Redis pub/sub handle comfortably. Revisit if we need multi-consumer replay or more than about 5k msg/s sustained.

## 4. Key technical decisions

Short-form ADRs. Each will move to `docs/architecture/adr/` when it needs more depth.

| # | Decision | Rationale | Alternatives considered |
|---|---|---|---|
| ADR-001 | Modular monolith, one Python distribution with top-level packages `core`, `database`, `integrations`, `services`, `ml`, `chainsight_api` | Matches the requested repo layout, keeps a single deploy and a single dependency set, and import rules are test-enforced | Microservices (premature); one `chainsight` namespace package (cleaner imports but hides the module layout) |
| ADR-002 | PostgreSQL 16 + PostGIS + TimescaleDB (`timescale/timescaledb-ha` image) | One store for relational, spatial, and time-series data, and joins across all three are common (positions ⋈ vessels ⋈ ports) | Separate TSDB (InfluxDB/ClickHouse): extra ops, cross-store joins |
| ADR-003 | `geography(Point, 4326)` for positions and ports | Distances in metres on the sphere, correct across the antimeridian | `geometry` + projection: faster but error-prone at global scale |
| ADR-004 | TimescaleDB hypertables only for high-volume append-only series (`vessel_positions`, `port_observations`, `maritime_features`), with TimescaleDB optional at migration time | Compression + retention where it matters; dev and CI still work on plain PostGIS | Hypertables everywhere: constrains unique keys and FKs |
| ADR-005 | psycopg 3 for both async (API) and sync (Alembic, Celery) | One driver, one URL scheme (`postgresql+psycopg://`) | asyncpg + psycopg2 |
| ADR-006 | Celery + Redis for background work; a dedicated process for the AIS WebSocket | Celery suits scheduled/retryable jobs, not persistent sockets | Arq/Dramatiq (smaller ecosystem); Kafka (not justified yet) |
| ADR-007 | Enum-like columns stored as `VARCHAR` + `CHECK` constraint | Adding a value is a cheap migration; no `ALTER TYPE` transactional pitfalls | Native PG enums |
| ADR-008 | Append-only predictions and milestone events | Auditability, honest model evaluation, no lost history | Mutable "current ETA" column (kept only as a derived view) |
| ADR-009 | Internal event model based on DCSA Track & Trace (`equipmentEventTypeCode`, `transportEventTypeCode`, `eventClassifierCode` = PLN/EST/ACT) | Industry standard that maps naturally to PLANNED / ESTIMATED / ACTUAL | Ad-hoc per-carrier statuses |
| ADR-010 | Frontend calls only the ChainSight API via a same-origin Next.js rewrite | No provider keys in the browser, no CORS complexity in production | Direct browser → provider calls (leaks keys) |
| ADR-011 | uv for Python dependency management, npm for the web app | Fast, lockfile-based, few tools | Poetry; pnpm (adds a tool for a single JS package) |
| ADR-012 | MLflow (local file or SQLite store in dev) for experiment tracking and model registry; predictions reference `model_versions.id` | Reproducibility and model lineage on every prediction row | W&B (hosted, account required) |
| ADR-013 | scikit-learn `HistGradientBoosting*` instead of LightGBM/XGBoost for Phase 4 models | Same histogram-based gradient boosting family with native quantile loss and class weights; no OpenMP system library (LightGBM failed to load on macOS without `libomp`), so `uv sync` alone is enough in dev and CI | LightGBM (needs `brew install libomp` on macOS, faster on large data); XGBoost (heavier wheel). Revisit if training sets outgrow HGB |

## 5. Cross-cutting concerns

### 5.1 Provenance and time

Every ingested fact carries:

- `source_id` → `data_sources.id`, the provider and adapter that produced it.
- `source_ref`: the provider's own identifier (message ID, event ID), when one exists.
- `observed_at` / `event_at`: when the thing happened according to the source (UTC).
- `ingested_at`: when ChainSight stored it (UTC, set by the DB).
- `available_at` (research/ML only): when a consumer could first have known it. Usually `ingested_at`, or the provider's publication time for periodic datasets.

All timestamps are `TIMESTAMPTZ` and normalized to UTC at the adapter boundary. Source timestamps with no timezone are rejected or explicitly assumed UTC with a data-quality flag.

### 5.2 Data quality

Validation runs at two layers. Pydantic DTOs at the adapter boundary check types, ranges (lat ∈ [-90, 90], SOG ∈ [0, 102.2] kn per the AIS spec), and required fields. Services then run semantic checks: implausible jumps (implied speed > 60 kn), timestamps in the future, stale messages. Rejected records are counted in `ingestion_logs`, sampled for debugging, and never silently dropped.

### 5.3 Security

- Secrets come only from environment variables (`.env` in dev, a secret manager in deployment) and are loaded into `pydantic.SecretStr` so they don't appear in logs or reprs.
- API keys are stored as SHA-256 hashes with a short public prefix for lookup. Users are authenticated with Argon2id password hashes, short-lived JWTs in httpOnly cookies and rotating refresh sessions.
- RBAC roles: `owner`, `admin`, `analyst`, `viewer`, scoped per organization. Every org-owned table has `organization_id`, and repository functions require it, so isolation is enforced in one place.
- Webhook endpoints (carrier push) verify HMAC signatures with timestamp tolerance and reject replays.
- Full details, including the known limitations: [security.md](security.md).

### 5.4 Observability

- Structured JSON logs (structlog) with `request_id` and, for ingestion, `data_source` and `ingestion_log_id`.
- `/api/v1/health` (liveness) and `/api/v1/health/ready` (DB, extensions, migration revision, Redis).
- Prometheus metrics (`/metrics`, worker :9808, AIS consumer :9809), a Grafana overview dashboard, alert rules, and optional OpenTelemetry traces. See [observability.md](../operations/observability.md). Model drift is tracked by the live evaluation in `model_evaluations` (Phase 4) rather than in Grafana.

### 5.5 Failure handling

Every external integration can fail independently. A provider outage marks its `data_sources.status = degraded|error`, raises a `system_alerts` row, and the UI shows stale-data banners. Pages that depend on that provider still render, using the last known data with an age badge.

## 6. Deployment topology

- **Local:** `docker compose up` (db, redis, migrate, api, worker, beat, web). Native dev is also supported: `uv run uvicorn …` and `npm run dev` against the compose DB.
- **Target (Phase 6):** container images built in GitHub Actions and pushed to GHCR, deployed to a single VM with Compose or to a small managed Kubernetes cluster. Managed Postgres needs both PostGIS and TimescaleDB (Timescale Cloud, or self-managed).

## 7. Technical risks

| Risk | Impact | Mitigation |
|---|---|---|
| Carrier API access needs commercial agreements or approval | Module A may only have mock/CSV data | Adapter contract + mock + CSV import, with integration status shown in the UI ([integration-strategy.md](integration-strategy.md)) |
| AISStream is free and best-effort with no SLA; coverage is terrestrial-only and patchy offshore | Gaps in vessel tracks, ETA model bias toward coastal areas | Freshness monitoring, explicit "gap" segments on tracks, missing-AIS anomaly class, provider abstraction for paid AIS later |
| Mapping vessels to carriers (operator vs. owner vs. slot charter) is ambiguous | Wrong fleet attribution | `vessel_carrier_history` with source + confidence; never infer silently |
| Small or unlabeled training data | Weak models | Baselines first, honest metrics with confidence intervals, label models trained on synthetic data as synthetic |
| Look-ahead bias in research | Misleading backtests | Point-in-time joins on `available_at`, walk-forward evaluation, leakage tests |
| Position volume growth | Storage and query cost | Hypertable compression after 7 days, downsampled continuous aggregates, retention policy for raw positions |
| Licensing of third-party data | Redistribution limits | `data_sources.license_note` + attribution in UI; no bundling of raw third-party data in the repo |
