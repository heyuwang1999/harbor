# Override when docker needs sudo:  DOCKER="sudo docker" make demo
DOCKER ?= docker
COMPOSE := $(DOCKER) compose -f deploy/compose/docker-compose.yml
# Local services must bypass any shell HTTP proxy.
export NO_PROXY := localhost,127.0.0.1
export no_proxy := localhost,127.0.0.1

.PHONY: help install infra infra-down demo demo-down demo-logs app migrate seed dev-api dev-worker dev-web test test-unit lint typecheck check spike-0001

help: ## List targets
	@grep -E '^[a-zA-Z0-9_-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-12s %s\n", $$1, $$2}'

install: ## Install Python and Node dependencies
	cd services/api && uv sync
	cd tools/mock-llm && uv sync
	pnpm install

infra: ## Start Postgres (ParadeDB), Valkey and mock-llm
	$(COMPOSE) up -d --wait

infra-down: ## Stop the local stack (keeps data volume)
	$(COMPOSE) down

app: ## Run the API in containers as well (no web app)
	$(COMPOSE) --profile app up -d --build --wait

demo: ## Build, migrate, seed and open the full demo
	$(COMPOSE) --profile demo --profile seed build
	$(COMPOSE) up -d --wait postgres valkey mock-llm
	$(COMPOSE) --profile seed run --rm seed
	$(COMPOSE) --profile demo up -d --wait api web
	@echo ""
	@echo "  Harbor demo ready:  http://localhost:3000"
	@echo "  API docs:           http://localhost:8000/docs"
	@echo "  Try: 請病假要唔要醫生紙？ · switch role to Visitor and ask about salary bands"
	@echo ""

demo-down: ## Stop the demo (add ARGS=-v to delete the database volume)
	$(COMPOSE) --profile demo --profile seed down $(ARGS)

demo-logs: ## Follow demo logs
	$(COMPOSE) --profile demo logs -f

migrate: ## Apply database migrations against local infra
	cd services/api && uv run alembic upgrade head

seed: ## Load the demo corpus (idempotent)
	cd services/api && uv run harbor-demo-seed

dev-api: ## Run the API with reload against local infra
	cd services/api && HARBOR_LOG_JSON=false uv run uvicorn harbor_api.app:create_app --factory --reload --port 8000

dev-worker: ## Run the ingestion worker against local infra
	cd services/api && HARBOR_LOG_JSON=false uv run celery -A harbor_api.workers.celery_app worker --loglevel=info --concurrency=2

dev-web: ## Run the web app with hot reload
	pnpm --filter @harbor/web dev

test: ## Run every test (integration tests need `make infra`)
	cd services/api && uv run pytest -q
	cd tools/mock-llm && uv run pytest -q -p no:warnings
	pnpm -r test

test-unit: ## Only the tests that need no services
	cd services/api && uv run pytest tests/unit -q
	cd tools/mock-llm && uv run pytest -q -p no:warnings
	pnpm -r test

lint: ## Lint and format-check everything
	cd services/api && uv run ruff check . && uv run ruff format --check .
	cd tools/mock-llm && uv run ruff check . && uv run ruff format --check .
	pnpm -r lint

typecheck: ## Static type checks
	cd services/api && uv run mypy src tests
	cd tools/mock-llm && uv run mypy src tests
	pnpm -r typecheck

check: lint typecheck test ## Everything CI runs before merge

spike-0001: ## Re-run the retrieval storage spike (needs `make infra`)
	uv run spikes/0001-lexical-search/run.py
