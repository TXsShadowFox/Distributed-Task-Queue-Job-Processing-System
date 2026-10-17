# FlowForge developer commands. Run `make help` for the list.
# Python runs through uv (uv run ...); the dashboard and E2E suite use npm.

SHELL := bash
.DEFAULT_GOAL := help
UV ?= uv
PY := $(UV) run python
PYTEST := $(UV) run pytest
WEB := apps/web
E2E := tests/e2e
SRC := packages/shared-schemas/src packages/core/src packages/python-sdk/src apps/api/src \
       services/worker/src services/scheduler/src

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------------------------------------- setup
.PHONY: setup
setup: ## Install Python and Node dependencies, create .env with generated secrets
	$(UV) sync --frozen
	cd $(WEB) && npm ci --no-audit --no-fund
	cd $(E2E) && npm ci --no-audit --no-fund
	$(PY) scripts/devstack.py init-env

# ---------------------------------------------------------------------------------------------- local stack
.PHONY: dev-up dev-down dev-status migrate seed
dev-up: ## Start PostgreSQL and Redis natively (no Docker needed) and apply migrations
	$(PY) scripts/devstack.py up
	$(UV) run flowforge-admin migrate

dev-down: ## Stop the native PostgreSQL and Redis
	$(PY) scripts/devstack.py down

dev-status: ## Show whether PostgreSQL and Redis are running
	$(PY) scripts/devstack.py status

migrate: ## Apply database migrations
	$(UV) run flowforge-admin migrate

seed: ## Submit real demo tasks, a workflow run and schedules through the API
	$(PY) scripts/seed_demo.py

.PHONY: api worker scheduler web
api: ## Run the API on :8000
	$(UV) run flowforge-api

worker: ## Run a worker (metrics on :9101)
	$(UV) run flowforge-worker

scheduler: ## Run the scheduler / control plane (metrics on :9102)
	$(UV) run flowforge-scheduler

web: ## Run the dashboard dev server on :3000
	cd $(WEB) && npm run dev

# ---------------------------------------------------------------------------------------------- quality
.PHONY: lint format typecheck openapi check verify
lint: ## Lint Python (ruff) and the dashboard (eslint)
	$(UV) run ruff check .
	$(UV) run ruff format --check .
	cd $(WEB) && npm run lint

format: ## Auto-format Python
	$(UV) run ruff format .
	$(UV) run ruff check --fix .

typecheck: ## Type-check Python (mypy --strict) and TypeScript
	$(UV) run mypy $(SRC) tests
	cd $(WEB) && npm run typecheck

openapi: ## Regenerate the OpenAPI document and the dashboard's API types
	$(PY) scripts/export_openapi.py
	cd $(WEB) && npm run gen:api

check: lint typecheck test ## Lint, types and tests (no E2E, chaos or Docker)

verify: lint typecheck test chaos security-check ## Every check that runs without Docker; fails on the first error

# ---------------------------------------------------------------------------------------------- tests
.PHONY: test test-unit test-integration test-web chaos e2e test-e2e screenshots
test: test-unit test-integration test-web ## Unit + integration (with coverage gate) + dashboard tests

test-unit: ## Pure unit tests (no services needed)
	$(PYTEST) tests/unit -q

test-integration: ## Integration tests against PostgreSQL + Redis, with the 85% coverage gate
	$(PYTEST) tests/unit tests/integration --cov -q

test-web: ## Dashboard unit tests (Vitest)
	cd $(WEB) && npm test

chaos: ## Fault injection: SIGKILLed workers, Redis/PostgreSQL partitions, scheduler crashes
	FLOWFORGE_RUN_CHAOS=1 $(PYTEST) tests/chaos -v

test-e2e: e2e ## Alias of e2e

e2e: ## Playwright against a running stack (FLOWFORGE_WEB_URL, default http://localhost:3000)
	cd $(E2E) && npx playwright test --project=desktop --project=mobile

screenshots: ## Regenerate docs/screenshots from a running stack with demo data
	cd $(E2E) && npx playwright test --project=screenshots

# ---------------------------------------------------------------------------------------------- performance
.PHONY: bench benchmark bench-quick bench-workloads
benchmark: bench ## Alias of bench

bench: ## Full benchmark matrix (100..50k jobs x 1..8 workers) -> docs/benchmarks/
	$(PY) -m tests.performance.benchmark

bench-workloads: ## I/O-bound and CPU-bound matrices and crash recovery -> docs/benchmarks/{io-bound,cpu-bound,recovery}
	$(PY) -m tests.performance.benchmark --workload io --jobs 100,1000,10000 --out docs/benchmarks/io-bound
	$(PY) -m tests.performance.benchmark --workload cpu --jobs 100,1000,10000 --out docs/benchmarks/cpu-bound
	$(PY) -m tests.performance.benchmark --workload io --jobs 10000,10000,10000 --workers 4 \
		--kill-worker-after 0.3 --out docs/benchmarks/recovery

bench-quick: ## A two-minute benchmark smoke run -> tests/performance/results/
	$(PY) -m tests.performance.benchmark --jobs 100,1000 --workers 1,2 --out tests/performance/results

# ---------------------------------------------------------------------------------------------- security
.PHONY: security security-check
security-check: security ## Alias of security

security: ## pip-audit (locked deps), bandit, npm audit (production deps)
	mkdir -p .devtools
	$(UV) export --frozen --no-dev --no-emit-workspace > .devtools/requirements.audit.txt
	$(UV) run pip-audit --strict --disable-pip -r .devtools/requirements.audit.txt
	$(UV) run bandit -q -r $(SRC) -c pyproject.toml
	cd $(WEB) && npm audit --omit=dev --audit-level=high

# ---------------------------------------------------------------------------------------------- docker
.PHONY: docker-build up down logs
docker-build: ## Build the backend and dashboard images
	docker compose build

up: ## Start the whole platform with docker compose (http://localhost:8080)
	docker compose up -d --build

down: ## Stop the docker compose stack (data volumes are kept)
	docker compose down

logs: ## Follow the docker compose logs
	docker compose logs -f --tail=100

# ---------------------------------------------------------------------------------------------- housekeeping
.PHONY: clean
clean: ## Remove caches and build output (keeps .env and .devtools data)
	rm -rf .mypy_cache .ruff_cache .pytest_cache htmlcov coverage.xml .coverage
	rm -rf $(WEB)/.next $(E2E)/test-results $(E2E)/playwright-report
	find . -name __pycache__ -type d -prune -not -path "./.venv/*" -exec rm -rf {} +
