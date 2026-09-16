# Harbor: guide for AI coding agents

Harbor is a bilingual (EN / 繁 / 简 / written Cantonese) RAG knowledge assistant. It is a portfolio and freelance product, so **production quality and honest, measured claims matter more than feature count**.

- Approved plan and milestones: `docs/specs/harbor-v1-plan.md` (current milestone: **M0**)
- Architecture decisions: `docs/adr/` (read ADR-0001 before touching retrieval or the database)

## Layout
| Path | What | Stack |
|---|---|---|
| `services/api` | Backend, package `harbor_api` | Python 3.12, uv, FastAPI, Pydantic v2, SQLAlchemy 2 async, structlog |
| `tools/mock-llm` | OpenAI-compatible fake provider for tests, E2E and load tests | FastAPI |
| `apps/web` | Staff app + admin console | Next.js 16 App Router, TS, Tailwind v4 |
| `deploy/compose` | Local stack: ParadeDB (Postgres 18 + pg_search + pgvector), Valkey, mock-llm | Docker Compose |
| `spikes/` | Throwaway experiments whose results feed ADRs | uv inline scripts |
| `evals/` | Golden datasets, thresholds, reports | |

## Commands
- `make infra`: start the local stack. `make dev-api` / `make dev-web`: run with reload.
- `make check`: lint + typecheck + tests (the same gates as CI). **Run it before saying work is done.**
- Single test: `cd services/api && uv run pytest tests/test_health.py -q`
- The shell has an HTTP proxy. Local calls need `NO_PROXY=localhost,127.0.0.1` (the Makefile sets it).

## Rules
- **Next.js 16 differs from older versions.** Read `apps/web/node_modules/next/dist/docs/` before writing web code (see `apps/web/AGENTS.md`). Use `next typegen` before `tsc`.
- **Tenant and ACL filters always go inside SQL** (never filter after retrieval). Filtered vector search must follow ADR-0001: exact search over the filtered set by default; HNSW only with `iterative_scan`.
- Index Chinese text through OpenCC `hk2s` normalisation (`text_norm`). Show and cite the original `text`.
- **Never call real LLM providers in tests**; use `tools/mock-llm`. Real-provider runs belong to evals only.
- **Metrics in README/docs must come from a committed, reproducible script or eval report.** Targets in the plan are not results.
- Python: `ruff` + `mypy --strict` clean. Full-width CJK punctuation is allowed (RUF001–003 disabled).
- Pin versions (images, GitHub Actions by SHA). ParadeDB upgrades must re-run `make spike-0001`.
- Keep comments sparse and explain *why*. Match the existing style of each package.
- Don't commit or push unless Holden asks.
