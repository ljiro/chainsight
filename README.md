# ChainSight

Open-source maritime intelligence and quantitative analytics: real-time vessel tracking, multi-carrier shipment visibility, predictive logistics models, and alternative-data research, with honest data provenance throughout.

> **Status: Phase 3 (Shipment tracking).** Live AIS fleet map with vessel registry, port calls and port weather; shipment tracking through a carrier adapter framework (generic DCSA Track & Trace adapter, a clearly labeled demo carrier, and CSV import) with planned/estimated/actual milestones and delay detection against the original plan. Predictive models (Phase 4) are not built yet, and the UI says so. See [TASKS.md](TASKS.md) and the [roadmap](docs/architecture/development-roadmap.md).

## Principles

- **Real, historical, synthetic, and unavailable data are always distinguished.** Every data source has a visible *data mode* and *freshness*.
- **AIS shows where a vessel is, never where a container is.** Shipments link to vessels only through carrier-reported assignments.
- **No scraping, and no pretending.** If a carrier API is not accessible, ChainSight ships an adapter contract, a mock, CSV import, and a clearly labeled status.
- **Predictions are append-only** and carry model version, input timestamp, and the eventual actual outcome.
- **Research, not trading.** Quant features are aligned on *availability* time to avoid look-ahead bias.

## Architecture

A modular monolith: FastAPI + Celery on PostgreSQL/PostGIS/TimescaleDB + Redis, with a Next.js frontend.

```text
apps/web            Next.js 15 · React 19 · Tailwind v4 · shadcn-style UI · TanStack Query
apps/api            FastAPI app (package: chainsight_api)
core/               settings, logging, time utilities (shared kernel)
database/           SQLAlchemy models + Alembic migrations
integrations/       provider adapters (AIS, carriers, weather, ports): no DB access
services/           ingestion, normalization, domain services, Celery worker
ml/                 ML pipelines and quant_research (Phase 4+)
infrastructure/     Dockerfiles, monitoring, deployment
docs/architecture/  system design, database design, integration strategy, roadmap
```

Package import rules are enforced by a test ([tests/unit/test_architecture.py](tests/unit/test_architecture.py)). Design details: [system-design.md](docs/architecture/system-design.md).

## Quick start (Docker)

Prerequisite: Docker Desktop (or OrbStack) with Compose v2.

```bash
cp .env.example .env          # placeholders only; works as-is for local use
docker compose up --build
```

| Service | URL |
|---|---|
| Dashboard | http://localhost:3000 |
| API docs (OpenAPI) | http://localhost:8000/api/v1/docs |
| Liveness / readiness | http://localhost:8000/api/v1/health · `/api/v1/health/ready` |

The `migrate` service applies Alembic migrations and, on first start, imports the UN/LOCODE port registry (about 7 MB download; skipped if offline). The `ais-consumer` service defaults to `AIS_PROVIDER=simulator`: **synthetic** vessels, labeled as such everywhere. For real AIS, set `AIS_PROVIDER=aisstream` and `AISSTREAM_API_KEY` ([docs/integrations/ais.md](docs/integrations/ais.md)).

## Native development

Prerequisites: Python 3.12 + [uv](https://docs.astral.sh/uv/), Node.js 22, Docker (for Postgres and Redis).

```bash
cp .env.example .env
make infra        # Postgres (PostGIS + TimescaleDB) and Redis in Docker
make install      # uv sync + npm install
make migrate      # alembic upgrade head
make api          # http://localhost:8000
make worker       # Celery worker (separate terminal)
make ports        # import the UN/LOCODE port registry
make ais          # AIS consumer (AIS_PROVIDER=simulator | aisstream | replay)
make web          # http://localhost:3000 (proxies /api/v1 to the API)
```

## Tests

```bash
make test-unit                      # backend unit tests (no services needed)

# Integration tests use a throwaway database whose name must end in _test.
export TEST_DATABASE_URL=postgresql+psycopg://chainsight:change-me@localhost:5432/chainsight_test
uv run pytest                       # unit + integration (migration round-trip, drift check, API)

cd apps/web && npm test             # frontend component tests (Vitest + Testing Library)
make lint typecheck
```

CI ([.github/workflows/ci.yml](.github/workflows/ci.yml)) runs lint, type checks, migrations, and all tests against a TimescaleDB-HA service container, plus the frontend lint, typecheck, test, and build.

## Configuration

All configuration comes from environment variables ([.env.example](.env.example)). Provider credentials are read only by the backend and are never exposed to the browser. A blank key means "not configured", and the platform reports it that way.

## Data sources

See [integration-strategy.md](docs/architecture/integration-strategy.md) for each provider's access requirements, licensing, and data mode, and [docs/integrations/](docs/integrations/) for setup guides (AIS, ports, weather, carriers). Port codes and coordinates: UN/LOCODE (UNECE). Basemap: OpenFreeMap / OpenStreetMap contributors. Weather data, when enabled, is by [Open-Meteo.com](https://open-meteo.com/) (CC BY 4.0). AIS data, when enabled, comes from [aisstream.io](https://aisstream.io/).

## License

[MIT](LICENSE). Third-party data remains subject to its providers' terms.
