SHELL := /bin/bash
.DEFAULT_GOAL := help

# Load .env (if present) so native commands use the same configuration as Compose.
ifneq (,$(wildcard .env))
include .env
export
endif

help: ## Show targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

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
web: ## Run the Next.js dev server
	cd apps/web && npm run dev

# --- quality ----------------------------------------------------------------------------
lint: ## Lint backend and frontend
	uv run ruff check .
	uv run ruff format --check .
	cd apps/web && npm run lint && npm run typecheck
typecheck: ## Static type checks (backend)
	uv run mypy core database apps/api/chainsight_api services
test: ## Run all tests (integration tests need TEST_DATABASE_URL)
	uv run pytest
	cd apps/web && npm test
test-unit: ## Backend unit tests only
	uv run pytest tests/unit

.PHONY: help up down logs infra install migrate api worker web lint typecheck test test-unit
