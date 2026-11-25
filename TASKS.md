# ChainSight — Task Checklist

Legend: `[x]` done **and verified** (ran/tested) · `[~]` implemented, verification pending · `[ ]` not started.
Nothing is marked `[x]` unless it has been executed and tested.

## Phase 0 — Inspection and planning
- [x] Inspect repository (empty; no prior work) and local toolchain
- [x] `docs/architecture/system-design.md`
- [x] `docs/architecture/database-design.md`
- [x] `docs/architecture/integration-strategy.md`
- [x] `docs/architecture/development-roadmap.md`
- [x] `TASKS.md`

## Phase 1 — Foundation
- [~] Monorepo layout (`apps/`, `core/`, `database/`, `integrations/`, `services/`, `ml/`, `infrastructure/`, `docs/`, `tests/`)
- [x] Root tooling: `pyproject.toml` (uv), `Makefile`, `.gitignore`, `.env.example`, `LICENSE` — `uv sync` OK on Python 3.12
- [~] Docker Compose: db (TimescaleDB-HA + PostGIS), redis, migrate, api, worker, beat, web
- [~] Dockerfiles for API/worker and web
- [x] Core: typed settings (secrets as `SecretStr`), structured logging, UTC time helpers — unit tested
- [~] Database: SQLAlchemy models + naming convention, async/sync sessions
- [~] Alembic `0001_foundation` — offline SQL render verified; not yet applied to a live DB — (orgs, users, members, api_keys, carriers, carrier_integrations, carrier_integration_logs, data_sources, ingestion_logs, ports, system_alerts, audit_logs; PostGIS; optional TimescaleDB)
- [~] Alembic `0002_reference_data` (carriers, platform carrier integrations, data sources)
- [~] FastAPI: request IDs, error envelope, `/api/v1/health`, `/api/v1/health/ready`, `/api/v1/data-sources`, `/api/v1/carriers`, OpenAPI — DB-free paths unit tested; DB endpoints await integration run
- [~] Celery app + heartbeat task + beat schedule — eager-mode tested; not yet run against Redis
- [~] Next.js app: layout, sidebar for all routes, dark/light theme, TanStack Query, API proxy rewrite
- [~] `/dashboard`: system health, data sources, carrier integration status, honest empty states
- [~] Placeholder pages for `/fleet`, `/carriers`, `/shipments`, `/vessels/[id]`, `/predictions`, `/research`, `/settings`
- [x] Backend tests: unit (settings, errors, health, worker, import boundaries) — 32 passed; ruff + mypy --strict clean
- [~] Backend tests: integration (migration round-trip, model/DB drift, seeds, API with DB)
- [~] Frontend tests (status badge, sidebar active state, data-sources loading/error/data states)
- [~] GitHub Actions CI (backend + migrations on TimescaleDB-HA service, frontend lint/typecheck/test/build)
- [~] Lockfiles: `uv.lock` generated; `apps/web/package-lock.json` pending (Node not installed); neither committed yet
- [ ] **Verification:** `docker compose up`, migrations applied to Postgres, integration tests, frontend install/lint/typecheck/test/build — *blocked: Docker and Node.js not installed yet*

## Phase 2 — Fleet tracking
- [ ] Migration: vessels, vessel_identifiers, vessel_carrier_history, vessel_positions (hypertable), vessel_position_sources, voyages, voyage_port_calls, port_areas, maritime_routes, port_observations
- [ ] Port registry importer (NGA WPI / UN/LOCODE) with provenance
- [ ] AIS message DTOs + parser (sentinel handling, validation)
- [ ] AISStream WebSocket adapter (subscription, reconnect/backoff, watchdog)
- [ ] Replay AIS provider (NDJSON) for dev/tests
- [ ] AIS consumer process: batching, dedup, quality flags, ingestion logs, Redis publish
- [ ] Vessel registry + entity resolution (MMSI/IMO, identifier history)
- [ ] Carrier ↔ vessel association (source + confidence)
- [ ] Freshness monitoring + alerts
- [ ] API: vessels, positions (bbox/time), tracks, ports; WebSocket live positions
- [ ] Open-Meteo collector (ports) + attribution
- [ ] UI: `/fleet` MapLibre map (filters, details, tracks, ports, freshness)
- [ ] UI: `/vessels/[id]`
- [ ] Tests: parser, dedup, entity resolution, consumer with mock WS, API, map state

## Phase 3 — Shipment tracking
- [ ] Migration: shipments, containers, shipment_items, shipment_vessels, shipment_events
- [ ] `CarrierAdapter` base: retries/backoff, rate limiting, validation, logging, status
- [ ] DCSA-based event normalization + code mapping tables
- [ ] Mock carrier adapter (synthetic, labeled)
- [ ] CSV shipment import (validation, ISO 6346 check digit)
- [ ] Real carrier adapter (if credentials available)
- [ ] Milestone derivation (PLANNED/ESTIMATED/ACTUAL) + rule-based delay detection
- [ ] API: shipments, containers, events, carrier integrations
- [ ] UI: `/shipments` (search, list, timeline, vessel, status), `/carriers`
- [ ] Tests: normalization, mocked HTTP adapters, CSV import, search UI, carrier filters

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
