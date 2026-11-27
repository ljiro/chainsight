SHELL := /bin/bash
.DEFAULT_GOAL := help

# Load .env (if present) so native commands use the same configuration as Compose.
ifneq (,$(wildcard .env))
include .env
export
endif

help: ## Show targets
	@grep -hE '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# --- full stack -------------------------------------------------------------------------
up: ## Build and start the full stack in Docker
	docker compose up --build -d
down: ## Stop the stack
	docker compose down
logs: ## Tail logs
	docker compose logs -f --tail=100

# --- native development (infra in Docker, app on host) ---------------------------------
infra: ## Start only Postgres and Redis
	docker compose up -d db redis
install: ## Install backend and frontend dependencies
	uv sync
	cd apps/web && npm install
migrate: ## Apply database migrations
	uv run alembic upgrade head
api: ## Run the API with reload
	uv run uvicorn chainsight_api.main:app --reload --port 8000
worker: ## Run a Celery worker
	uv run celery -A services.worker.app worker -l INFO -Q default,ingest,ml
ports: ## Import the UN/LOCODE port registry (downloads ~7 MB)
	uv run python -m services.ingestion.port_registry
ais: ## Run the AIS consumer (AIS_PROVIDER=simulator|aisstream|replay)
	uv run python -m services.ingestion.ais_consumer
user: ## Create a user: make user EMAIL=you@example.com ROLE=owner
	uv run python -m services.auth create-user --email $(EMAIL) --role $(or $(ROLE),owner)
backup: ## Back up the database now (Docker)
	docker compose --profile backup run --rm --entrypoint /scripts/backup.sh backup
preflight: ## Check .env.production and this host before deploying
	infrastructure/deployment/preflight.sh $(or $(ENV),.env.production)
verify: ## Check a deployment from outside: make verify URL=https://your-domain
	infrastructure/deployment/verify.sh $(URL)
web: ## Run the Next.js dev server
	cd apps/web && npm run dev

# --- quality ----------------------------------------------------------------------------
lint: ## Lint backend and frontend
	uv run ruff check .
	uv run ruff format --check .
	cd apps/web && npm run lint && npm run typecheck
typecheck: ## Static type checks (backend)
	uv run mypy core database integrations ml apps/api/chainsight_api services
test: ## Run all tests (integration tests need TEST_DATABASE_URL)
	uv run pytest
	cd apps/web && npm test
test-unit: ## Backend unit tests only
	uv run pytest tests/unit
e2e: ## Playwright smoke tests against the running stack (E2E_EMAIL, E2E_PASSWORD)
	cd apps/web && npm run e2e

.PHONY: help up down logs infra install migrate ports ais api worker user backup web lint typecheck test test-unit e2e
