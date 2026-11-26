# ChainSight — Development Roadmap

Status: Draft v1 (Phase 0). Detailed checklist: [`/TASKS.md`](../../TASKS.md).

Each phase ends with the app running locally, migrations applied, tests green, docs updated, and a review checkpoint before the next phase starts.

## Prioritization logic

1. **Foundation before features.** Schema conventions, provenance, and the error/health contracts are hard to retrofit.
2. **Free data first.** Fleet intelligence (AIS) can run on real data today. Shipment tracking depends on carrier access we may not get, so it follows and degrades to CSV/mock.
3. **Collect early, model later.** The AIS consumer must run for weeks before ETA and anomaly models have meaningful training data, so Phase 2 starts the clock on data collection.
4. **Baselines before ML, rules before models.**
5. **Research last.** It depends on stored history and point-in-time feature infrastructure.

## Phases

### Phase 0: Inspection and planning ✅
Architecture, database, integration strategy, roadmap, and `TASKS.md`.

### Phase 1: Foundation ✅
**Goal:** `docker compose up` gives a running stack with a migrated database and an honest, empty dashboard.
- Monorepo layout, Python project (uv), and the Next.js app.
- Compose: TimescaleDB-HA (PostGIS included), Redis, migrate, api, worker, beat, web.
- FastAPI: settings, structured logging, request IDs, error envelope, liveness/readiness, data-source and carrier listing endpoints.
- Alembic `0001` (foundation tables) and `0002` (reference data: carriers, data sources).
- Celery app with a heartbeat task.
- Dashboard shell: navigation for all routes, theme toggle, system health, data sources, and carrier integration status.
- Tests: unit, migration round-trip + drift, API against a real DB, and frontend component tests. CI workflow.

**Exit criteria:** all tests pass locally, and the readiness endpoint reports DB/PostGIS/Redis healthy.

### Phase 2: Fleet tracking ✅
- Schema: vessels, identifiers, vessel_carrier_history, vessel_positions (hypertable), voyages, port calls, port areas, maritime_routes, port_observations.
- Port registry import (UN/LOCODE, with 8 km geofences).
- AISStream adapter + consumer process + replay and simulator providers. Dedup, quality flags, freshness alerts.
- Vessel registry and entity resolution (MMSI/IMO).
- Carrier ↔ vessel associations, with source and confidence.
- API: vessels, positions (bbox + time window), tracks, ports, plus the WebSocket live feed.
- UI: `/fleet` MapLibre map (clustering, filters, track playback, freshness), `/vessels/[id]`.
- Open-Meteo collector for ports.
- **Starts AIS history collection.**

### Phase 3: Shipment tracking ✅ (awaiting review)
- Schema: shipments, containers, shipment_items, shipment_vessels, shipment_events.
- Carrier adapter base (retries, rate limits, logging, status) + mock carrier + CSV import.
- One real carrier adapter if credentials are available (first candidates: Maersk or Hapag-Lloyd via DCSA T&T).
- DCSA-based normalization and milestone derivation (PLANNED / ESTIMATED / ACTUAL).
- Delay detection rules (ACT/EST vs. original PLN).
- UI: `/shipments` search, list, and timeline. `/carriers` comparison.

### Phase 4: Predictive intelligence
- Training dataset builders with point-in-time joins, and dataset hashing.
- ETA: great-circle distance ÷ recent SOG baseline, then quantile gradient boosting (scikit-learn HistGradientBoosting, ADR-013), with time-based splits and MAE/MedAE/RMSE/within-6/12/24h.
- Delay classifier: logistic baseline → gradient boosting, with class weighting, calibration (isotonic), and PR-AUC.
- Rule-based anomaly detection (stops, deviations, speed changes, AIS gaps, long waits) → `system_alerts`.
- Congestion forecasting baseline (seasonal naive → gradient boosting on lags).
- MLflow tracking, the `model_versions` registry, append-only prediction tables, outcome back-fill, and live evaluation.
- UI: `/predictions`, and prediction widgets on shipment and vessel pages.

### Phase 5: Quantitative research
- `ml/quant_research`: point-in-time feature builders (PortWatch port/chokepoint activity, composites, AIS arrivals/departures/waits/deviations; D/W/M; rolling, WoW/YoY, z-scores). Delivered; see docs/research/methodology.md.
- Statistics: correlations, rolling correlations, OLS with HAC errors, ADF/KPSS, lead-lag cross-correlation, Granger (with caveats).
- Backtesting: walk-forward, costs, slippage, sizing, drawdown, Sharpe, turnover, and benchmark. Look-ahead guards on `available_at`.
- Experiments persisted, plus a reproducibility manifest (code SHA, data hash, config).
- UI: `/research`.

### Phase 6: Production hardening
- AuthN (Argon2id + JWT), API keys, RBAC, and org isolation tests.
- Rate limiting, webhook HMAC verification, and audit logging.
- Prometheus metrics, Grafana dashboards, and OpenTelemetry tracing.
- Backups (pgBackRest or scheduled `pg_dump`), a restore runbook, and deployment config. CI/CD image publishing.
- Playwright end-to-end smoke tests.

## External credentials checklist

| Needed for | Variable | Required? |
|---|---|---|
| Live AIS | `AISSTREAM_API_KEY` | Phase 2 (replay mode works without it) |
| Carrier tracking | `MAERSK_*`, `HAPAG_LLOYD_*`, … | Optional; mock + CSV otherwise |
| Trade data | `UN_COMTRADE_API_KEY` | Phase 5, optional |
| Market data | `FRED_API_KEY`, `EIA_API_KEY` | Phase 5 |
