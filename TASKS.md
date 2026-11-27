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
- [x] IMF PortWatch collector (delivered in Phase 5, where its publication lag matters)

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
- [x] Migration `0005`: model_versions, model_evaluations, eta/delay/congestion predictions, risk_assessments; append-only trigger (only outcome columns writable) — round-trip tested
- [x] Time-based splits with a leakage assertion; dataset hashing; point-in-time delay features (`available_at`)
- [x] ETA: distance ÷ speed baseline + quantile HistGradientBoosting (p10/p50/p90), promoted only if it beats the baseline; training-domain guard and censoring-aware fallback (ADR-013: HGB instead of LightGBM)
- [x] Delay: logistic baseline vs isotonic-calibrated HGB, class-weighted; PR-AUC/Brier/ECE + calibration table — **no model in production: no real shipment arrivals yet**; synthetic training is opt-in, labeled, and scores only synthetic shipments
- [x] Rule-based anomaly detection → `system_alerts` (stop, speed drop, AIS silence gated on feed continuity, long port wait, route deviation)
- [x] Congestion: hourly observations (no backdating, warm-up excluded) + seasonal naive / persistence forecast; intervals only after 24 scored errors
- [x] MLflow tracking (SQLite store) + `model_versions` registry; portable artifact URIs on a shared Docker volume
- [x] Outcome back-fill + live evaluation (`ml.evaluate` hourly); Celery schedule for anomalies, predictions, congestion
- [x] API: `/models`, `/predictions/{summary,eta,delay,congestion}`, `/vessels/{id}/eta`, `/shipments/{id}/delay-risk`, `/ports/{id}/congestion`, `/anomalies`
- [x] UI: `/predictions` (registry, ETA, delay risk, congestion chart, anomalies), vessel ETA + anomaly cards, shipment delay-risk card, dashboard high-risk KPI
- [x] Model cards: `docs/ml/`
- [x] Tests: backend 251 (append-only trigger, leakage, domain/saturation fallbacks, synthetic guardrail, API provenance); frontend 83
- [ ] Congestion gradient boosting on lags — *needs weeks of hourly history*
- [ ] ETA beyond final approach — *needs longer AIS history (labels are right-censored at the ingest window)*

## Phase 5 — Quantitative research
- [x] Migration `0006`: indicator_series + indicator_observations (hypertable, revisions), research_datasets, maritime_features (hypertable, versions), research_experiments, backtest_runs, backtest_metrics, research_signals; FRED source — round-trip + drift tested
- [x] IMF PortWatch collector (8 a priori ports + 28 chokepoints, 2019 →) and FRED collector (Brent, WTI, Henry Hub; API key optional) with documented, conservative availability rules; live values use first-seen time; revisions never overwrite
- [x] Point-in-time features: composites, D/W/M frequencies, level/MA/pct/YoY/z/diff transforms, availability = latest input; AIS-derived daily series
- [x] Statistics: correlation, lag correlation with noise band, rolling correlation, ADF/KPSS, OLS with HAC errors, Granger, out-of-sample R², permutation importance
- [x] Walk-forward backtester: costs, slippage, sign/linear sizing, long-only, Sharpe (+ Lo SE), drawdown, turnover, hit rate, information ratio, buy-and-hold benchmark; in-sample shown separately
- [x] Look-ahead tests: unpublished values never used; perturbing the future leaves past positions unchanged; oracle/noise sanity checks
- [x] Experiments with stated hypotheses, git SHA + data hash, per-fold parameters, stored positions; three seeded a priori examples (none shows an out-of-sample edge)
- [x] API: `/research/{indicators,features,market,analysis,importance,experiments}`; daily Celery refresh
- [x] UI: `/research` (explorer, analysis, experiments, new experiment)
- [x] Docs: `docs/research/methodology.md`
- [x] Tests: backend 279 (unit + integration incl. mocked PortWatch/FRED, revisions, reproducibility, API); frontend 92
- [ ] UN Comtrade / EIA collectors — *optional; need API keys*
- [x] Deflated Sharpe (Bailey & López de Prado) + Benjamini–Hochberg across experiments; trial Sharpes and OOS moments stored per run; API + UI column; none of the seeded experiments survives (DSR ≤ 0.06)

## Phase 6 — Production hardening
- [ ] Authentication (Argon2id, JWT), API keys, RBAC, org isolation
- [ ] Rate limiting, webhook HMAC verification, audit logging
- [ ] Prometheus metrics, Grafana dashboards, OpenTelemetry
- [ ] Backups + restore runbook
- [ ] Deployment configuration + image publishing
- [ ] Playwright E2E smoke tests
- [ ] `/settings` (org, integrations, API keys, users, source health)
