COMPOSE := docker compose -f deploy/compose/docker-compose.yml
# Local services must bypass any shell HTTP proxy.
export NO_PROXY := localhost,127.0.0.1
export no_proxy := localhost,127.0.0.1

.PHONY: help install infra infra-down app dev-api dev-web test lint typecheck check spike-0001

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

app: ## Run the full stack in containers, including the API
	$(COMPOSE) --profile app up -d --build --wait

dev-api: ## Run the API with reload against local infra
	cd services/api && HARBOR_LOG_JSON=false uv run uvicorn harbor_api.app:create_app --factory --reload --port 8000

dev-web: ## Run the web app with hot reload
	pnpm --filter @harbor/web dev

test: ## Run all unit tests
	cd services/api && uv run pytest -q
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
