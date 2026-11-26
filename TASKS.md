# ChainSight — Task Checklist

Legend: `[x]` done **and verified** (ran/tested) · `[~]` implemented, verification pending · `[ ]` not started. Caveats are noted inline in **bold**.
Nothing is marked `[x]` unless it has been executed and tested.

## Phase 0 — Inspection and planning
- [x] Inspect repository (empty; no prior work) and local toolchain
- [x] `docs/architecture/system-design.md`
- [x] `docs/architecture/database-design.md`
- [x] `docs/architecture/integration-strategy.md`
- [x] `docs/architecture/development-roadmap.md`
- [x] `TASKS.md`

## Phase 1 — Foundation
- [x] Monorepo layout (`apps/`, `core/`, `database/`, `integrations/`, `services/`, `ml/`, `infrastructure/`, `docs/`, `tests/`)
- [x] Root tooling: `pyproject.toml` (uv), `Makefile`, `.gitignore`, `.env.example`, `LICENSE` — `uv sync` OK on Python 3.12
- [x] Docker Compose: db (TimescaleDB-HA + PostGIS), redis, migrate, api, worker, beat, web — `docker compose up --build` verified, all services healthy
- [x] Dockerfiles for API/worker and web
- [x] Core: typed settings (secrets as `SecretStr`), structured logging, UTC time helpers — unit tested
- [x] Database: SQLAlchemy models + naming convention, async/sync sessions — no model/migration drift (integration test)
- [x] Alembic `0001_foundation` (orgs, users, members, api_keys, carriers, carrier_integrations, carrier_integration_logs, data_sources, ingestion_logs, ports, system_alerts, audit_logs; PostGIS; optional TimescaleDB) — applied on PostGIS 3.7 / TimescaleDB 2.32; up/down/up round-trip tested
- [x] Alembic `0002_reference_data` (carriers, platform carrier integrations, data sources)
- [x] FastAPI: request IDs, error envelope, `/api/v1/health`, `/api/v1/health/ready`, `/api/v1/data-sources`, `/api/v1/carriers`, OpenAPI — unit + integration tested, verified live
- [x] Celery app + heartbeat task + beat schedule — beat dispatches every 60 s, worker executes it, structured logs verified
- [x] Next.js app: layout, sidebar for all routes, dark/light theme, TanStack Query, API proxy rewrite — production build OK, proxy verified
- [x] `/dashboard`: system health, data sources, carrier integration status, honest empty states
- [x] Placeholder pages for `/fleet`, `/carriers`, `/shipments`, `/vessels/[id]`, `/predictions`, `/research`, `/settings` — all routes return 200
- [x] Backend tests: unit (settings, errors, health, worker, import boundaries) — 32 passed; ruff + mypy --strict clean
- [x] Backend tests: integration (migration round-trip, model/DB drift, seeds, API with DB) — 14 passed locally and in CI
- [x] Frontend tests (status badge, sidebar active state, data-sources loading/error/data states, API client) — 28 passed
- [x] GitHub Actions CI (backend + migrations on TimescaleDB-HA service, frontend lint/typecheck/test/build) — both jobs green
- [x] Lockfiles: `uv.lock`, `apps/web/package-lock.json`
- [x] **Verification:** `docker compose up`, migrations applied to Postgres, integration tests, frontend lint/typecheck/test/build

## Phase 2 — Fleet tracking
- [x] Migration `0003`: vessels, vessel_identifiers, vessel_carrier_history, vessel_positions (hypertable + 7-day compression), vessel_position_sources, voyages, voyage_port_calls, port_areas, maritime_routes, port_observations (hypertable), weather_observations — round-trip + drift tested
- [x] Port registry importer (UN/LOCODE, 11,836 seaports, 8 km geofences) with provenance — NGA WPI deferred (download endpoint refused automated requests)
- [x] AIS message DTOs + parser (sentinels, rejections, nanosecond timestamps, IMO checksum)
- [x] AISStream WebSocket adapter (subscription, reconnect/backoff, idle watchdog) — tested with a fake socket; **not yet run against the live service (no API key)**
- [x] Replay provider (recorded NDJSON, original timestamps) and simulator provider (synthetic, MID 200)
- [x] AIS consumer process: batching, dedup, quality flags, 60 s ingestion windows, failure accounting, Redis publish — verified in Docker
- [x] Vessel registry + entity resolution (MMSI/IMO/name history, MMSI reassignment via IMO)
- [x] Carrier ↔ vessel association (name heuristic, method + confidence)
- [x] Port-call and voyage detection from geofences (unknown arrivals kept as NULL)
- [x] Freshness monitoring + deduplicated alerts (beat, every minute)
- [x] API: fleet positions (GeoJSON), summary, approaching, vessels, track (gaps), port calls, ports, alerts; WebSocket live positions — verified live
- [x] Open-Meteo collector (ports) + attribution — verified against the live API
- [x] UI: `/fleet` MapLibre map (filters, details, tracks, ports, freshness, provenance banner) — **map canvas not visually verified (headless browser lacks WebGL)**
- [x] UI: `/vessels/[id]`; dashboard fleet activity, approaching ports, open alerts
- [x] Tests: backend 164 (unit + integration incl. pipeline, collectors, API, WebSocket); frontend 54
- [ ] Route-deviation detection (moved to Phase 4 anomaly detection)
- [x] AISStream stability: handshake/ping timeouts tuned after live measurement (handshake ~11.5 s); ingestion timing metrics
- [ ] IMF PortWatch collector (moved to Phase 5, where its publication lag matters)

## Phase 3 — Shipment tracking
- [x] Migration `0004`: shipments, containers, shipment_items, shipment_vessels, shipment_events (append-only); local workspace org, demo carrier, carrier-demo/csv-import sources — round-trip + drift tested
- [x] `CarrierAdapter` base: retries/backoff (Retry-After), token-bucket rate limiting, response validation, call records, status reporting
- [x] DCSA-based event normalization + code mapping (unknown codes kept raw and reported)
- [x] Generic DCSA Track & Trace adapter (API key / bearer / OAuth2) — tested with mocked HTTP; **not verified against any live carrier (no credentials)**
- [x] Demo carrier adapter (synthetic; only answers DEMO/ZZZU references)
- [x] CSV shipment import (validation incl. ISO 6346 + IMO checksums, timezone-aware timestamps; all-or-nothing; idempotent)
- [ ] Real carrier integration — *blocked: needs carrier API credentials (see docs/integrations/carriers.md)*
- [x] Milestone derivation (PLANNED/ESTIMATED/ACTUAL, write-once original plan) + rule-based delay detection
- [x] Honest integration statuses (authentication_required / unavailable / demo_data_only), synced at startup and every 10 min
- [x] API: shipments (search, summary, track, detail, refresh, CSV import), carrier performance
- [x] UI: `/shipments` (track, import, list, filters), `/shipments/[id]` (milestones, estimate history, vessel legs with AIS link), `/carriers` comparison, dashboard shipment KPIs
- [x] Tests: backend 225 (unit + integration incl. mocked DCSA carrier, CSV import, API); frontend 73

## Phase 4 — Predictive intelligence
- [ ] Migration: model_versions, model_evaluations, eta_predictions, delay_predictions, risk_assessments
- [ ] Point-in-time training dataset builders + dataset hashing
- [ ] ETA baseline + LightGBM/XGBoost; time-based CV; metrics
- [ ] Delay classifier with imbalance handling + calibration
- [ ] Rule-based anomaly detection → alerts
- [ ] Congestion forecasting baseline
- [ ] MLflow tracking + model registry integration
- [ ] Append-only prediction persistence + outcome back-fill + live evaluation
- [ ] UI: `/predictions`
- [ ] Tests: feature schemas, temporal separation, output validation, reproducibility

## Phase 5 — Quantitative research
- [ ] Migration: research_datasets, maritime_features (hypertable), research_experiments, research_signals, backtest_runs, backtest_metrics
- [ ] Feature engineering (configurable frequency)
- [ ] PortWatch + FRED/EIA collectors with `available_at`
- [ ] Statistical analysis toolkit
- [ ] Walk-forward backtester (costs, slippage, sizing, drawdown, Sharpe, turnover, benchmark)
- [ ] Look-ahead / leakage tests
- [ ] UI: `/research`
- [ ] Research methodology docs + limitations

## Phase 6 — Production hardening
- [ ] Authentication (Argon2id, JWT), API keys, RBAC, org isolation
- [ ] Rate limiting, webhook HMAC verification, audit logging
- [ ] Prometheus metrics, Grafana dashboards, OpenTelemetry
- [ ] Backups + restore runbook
- [ ] Deployment configuration + image publishing
- [ ] Playwright E2E smoke tests
- [ ] `/settings` (org, integrations, API keys, users, source health)
