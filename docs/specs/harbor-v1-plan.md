# Harbor v1: Bilingual RAG Knowledge Assistant (Implementation Plan)

## Context

Harbor is project 1 of 3 in `/home/heyu/Projects/holden-wang/ai-projects-plan.md`. Its purpose is to close Holden's biggest gaps for AI roles (production RAG, vector search, evals, Kubernetes; see `holden-wang-profile.md` §9 and §14). It should also become a sellable freelance product.

**Decisions confirmed with Holden:**
- Enterprise-grade core, demoed as a **bilingual HK SME assistant** (staff Q&A + website widget + WhatsApp).
- **VPS + k3s** hosting.
- **20–25 h/week**.
- Launch providers: **OpenAI + Anthropic + Qwen + DeepSeek**. Local models move to post-v1.

**Market findings (Sep 2026) that shape the scope:**
- HK's **BUD Fund has explicitly covered AI website chatbots since June 2026**, and 55% of HK SMEs use or plan to use AI tools. **WhatsApp is the main customer channel** for them.
- WhatsApp's 2026 policy bans *general-purpose* AI bots but allows **business-scoped** customer-service bots. Harbor's grounded answers and refusals keep it compliant.
- **OpenAI and Anthropic direct APIs are not officially available in HK.** HK enterprises use **Azure OpenAI** or Chinese models. So Harbor needs an Azure OpenAI endpoint option and region-aware model profiles, with Anthropic direct only for non-HK deployments.
- 2026 enterprise RAG baseline:
  - Hybrid dense + sparse retrieval with RRF, then reranking (+8–14 pts recall@10).
  - **Access control enforced at retrieval time**.
  - Continuous evals (context precision/recall, faithfulness, relevancy).
  - Agentic multi-step retrieval for complex queries.
  - Retrieval, not generation, is the failure point 73% of the time.
- PCPD (HK privacy regulator) guidance to align with: AI Model Personal Data Protection Framework (2024), GenAI employee checklist (2025), **Agentic AI framework (Aug 2026)**.
- ParadeDB `pg_search` has a **Jieba** Chinese BM25 tokenizer. Docling (v2.126, Sep 2026) adds a fast native PDF path and Granite-Docling VLM.

**Outcome:** a deployed, multi-tenant, evaluated, observable RAG product in about 8 weeks. It comes with a public demo, published eval and benchmark numbers, bilingual docs and demo videos, and real metrics for the resume.

---

## Repo & Stack

New monorepo at **`/home/heyu/Projects/harbor`** (`git init`; public GitHub repo, Apache-2.0).

```
harbor/
  apps/web/              Next.js (App Router, TS, Tailwind v4, shadcn/ui, TanStack Query, next-intl en/zh-HK/zh-CN)
  apps/widget/           iframe chat app + tiny loader script (Vite, Preact)
  packages/chat-client/  shared TS SSE client + types (used by web & widget)
  packages/api-types/    generated from FastAPI OpenAPI (openapi-typescript + openapi-fetch)
  services/api/          Python 3.12, uv, FastAPI, Pydantic v2, SQLAlchemy 2 (async asyncpg in API, sync psycopg in workers), Alembic, Celery+Redis
    harbor/{api,core,models,schemas,auth,ingestion,connectors,retrieval,generation,llm,channels,handoff,analytics,evals,workers}
  tools/mock-llm/        OpenAI-compatible fake server (configurable latency/errors/stream) for tests & load tests
  evals/{datasets,thresholds.yaml,reports/}
  deploy/{compose,helm/harbor,k3s,grafana}
  docs/{adr,specs,runbook.md,security.md,pdpo-data-handling.md,owasp-llm-top10.md,user-guide.{en,zh-HK}.md,admin-guide.{en,zh-HK}.md,eval-report.md,ai-workflow.md}
  .github/workflows/{ci.yml,evals.yml,deploy.yml}
  CLAUDE.md, Makefile, pnpm-workspace.yaml
```

**Infra:**
- Database: **ParadeDB image** (Postgres + pgvector + pg_search) managed by CloudNativePG, with backups and WAL archive to **Cloudflare R2**.
- Cache/queue: **Valkey/Redis**.
- File storage: R2 (S3 API), local-disk backend in dev.
- Auth: **Better Auth** (Next.js) with organization plugin (tenants, roles, invites, teams as ACL groups) + JWT plugin. FastAPI verifies tokens via JWKS.
- Monitoring: **OpenTelemetry → Grafana Cloud free tier** via Alloy (keeps the VPS lean). **Sentry** for errors. Uptime Kuma status page.
- Edge: Cloudflare (DNS/WAF/Turnstile), cert-manager.
- VPS: 8 vCPU / 16 GB / 160 GB+ NVMe (~USD 30–50/mo). Staging and prod namespaces.

---

## Architecture

```
Staff web app ─┐                        ┌─► Retrieval: normalize(OpenCC) → [pgvector HNSW ‖ pg_search BM25(jieba)]
Widget iframe ─┼─SSE─► FastAPI /v1 ─────┤      (tenant + ACL filter inside SQL, RLS) → RRF → rerank → parent expand → pack
WhatsApp ──────┘  (webhook→queue)       ├─► Answerability gate → Generation (stream, [S#] citations) → citation validation
                                        ├─► LLM layer: OpenAI | Azure OpenAI | Anthropic | Qwen(DashScope intl) | DeepSeek
                                        │     tier profiles (small/main/judge), fallback chain, breaker, budget caps
Connectors (upload/crawler/Drive) ─► Celery: fetch→store(R2)→parse(Docling)→chunk→detect lang→embed→atomic swap
Analytics CronJobs: usage rollups · topic clustering · content gaps · weekly insight · retention · nightly evals
```

---

## Core Design (key decisions; each becomes an ADR in `docs/adr/`)

### Data model (Alembic)
- **Tenancy & access:** `tenants` (= Better Auth org id), `groups`, `sources`, `documents` (content_hash, version, status, visibility `public|internal|restricted`, language), `document_acl`.
- **`chunks`:**
  - Text fields: `text`, `text_norm` (OpenCC t2s, used only for indexing), `heading_path[]`, `parent_id`.
  - Location: `page_from/to`, `bbox` (for citation highlighting).
  - Vectors: `embedding vector(N)`, `embedding_model`.
  - Denormalised filters: `tenant_id`, `visibility`, `allowed_principals uuid[]`.
- **Conversations:** `conversations` (channel, status `bot|handoff|closed`, hashed+encrypted external contact), `messages` (citations, confidence, answer_type `answered|refused|handoff`, model, tokens, cost, latency), `retrieval_traces` (per-candidate dense/BM25/RRF/rerank ranks), `feedback`, `leads`.
- **Evals:** `eval_datasets/cases/runs/results`.
- **Ops:** `audit_log`, `widget_keys` (allowed origins), `channel_accounts`, `usage_daily`.
- **Tenant isolation, two layers:** a repository layer that always scopes by tenant, plus **Postgres Row-Level Security** as a backstop (`SET LOCAL app.tenant_id` per transaction).
- Filtered HNSW uses pgvector **iterative index scans** so recall holds under ACL filters.

### Ingestion (Celery; queues `ingest-heavy` concurrency 1–2 and `default`; idempotent by content_hash; `acks_late`)
1. Validate: magic bytes, size limits, zip-bomb guard. Store the original in R2.
2. **Docling:** native PDF path for digital PDFs, OCR (RapidOCR, zh-Hant/Hans) when there is no text layer. Keep tables, reading order, pages and bboxes.
3. **Heading-aware HybridChunker:** child chunks of ~400 tokens for retrieval, parent sections up to ~1,500 tokens for generation. Prepend a contextual header (title + heading path) to the embedding text.
4. Detect language per chunk. Build `text_norm`.
5. Batch-embed via a provider interface: OpenAI `text-embedding-3-small` vs. Qwen `text-embedding-v4`, chosen by eval.
6. **Atomic version swap** in one transaction (no half-indexed documents). Propagate deletions. Report progress to the UI.
7. Re-embedding job for model changes (shadow column, then cutover).

### Connectors (v1)
- **Upload:** multi-file and zip.
- **Website crawler:** sitemap/robots-aware, page and depth limits, scheduled recrawl with content-hash diff. This is essential for SME customer bots.
- **Google Drive:** OAuth, folder picker, Changes API incremental sync, Drive permissions → `document_acl` via email/group mapping.
- Post-v1: SharePoint/OneDrive (Graph), Confluence, Notion.

### Query pipeline (`harbor/retrieval`, `harbor/generation`)
1. **Guard:** length, rate limit, tenant daily token budget.
2. **Small model, structured output:** standalone query rewrite (uses history) + language/script + intent `question|smalltalk|handoff_request|lead_intent|out_of_scope`.
3. Parallel dense top-50 and BM25 top-50, with the same tenant/ACL/visibility SQL filter. Widget and WhatsApp see only `public` documents.
4. **RRF (k=60) → rerank top-30:** DashScope `gte-rerank`-class model, with LLM listwise rerank as fallback, chosen by eval. Then keep top 6–8, expand to parents, dedupe, and pack to a token budget.
5. **Answerability gate:** if the rerank score is below a threshold calibrated on the eval set, refuse (staff channel) or offer handoff/lead capture (customer channels).
6. **Generation:**
   - Instruction hierarchy; sources wrapped in delimiters with `[S#]` ids; no tools at this step.
   - Answer in the user's script (繁 for zh-HK).
   - Stable prompt prefix for provider prompt caching.
7. **Post-process:**
   - Drop citation ids that match no retrieved source.
   - Mark as low confidence if an answer has no valid citations.
   - Strip links and images that don't come from the sources (prevents exfiltration).
   - Persist the message and its retrieval trace.
8. **Exact-answer cache** for repeated FAQ questions, keyed by (tenant, normalised question, index version) and invalidated on re-index.
9. **Agentic mode (M6, if on track):** a router sends complex or multi-part questions to decompose → retrieve per sub-query → sufficiency check (≤3 rounds). Kept only if the multi-hop eval subset improves enough to justify the cost.

### SSE protocol (`docs/specs/chat-stream.md`, shared by web + widget)
- Events: `meta` → `status(retrieving|generating)` → `delta*` → `citations` → `done{answer_type, confidence, usage}` | `error`.
- Heartbeats, `Last-Event-ID`, k8s preStop drain.
- WhatsApp is non-streaming.

### LLM layer (`harbor/llm`; a preview of Switchyard)
- **Two adapters:**
  - **OpenAI-compatible:** OpenAI, **Azure OpenAI**, Qwen DashScope international compatible-mode, DeepSeek.
  - **Anthropic native.**
- **Structured outputs:** native JSON schema where supported, JSON mode plus Pydantic validation and one repair retry elsewhere.
- **Tier profiles per tenant** (`small`, `main`, `judge`). Region presets:
  - `hk` = Azure OpenAI + Qwen
  - `global` = Anthropic/OpenAI
  - `cn-friendly` = Qwen + DeepSeek (DeepSeek opt-in, with a data-residency note)
- **Reliability & cost:**
  - Timeouts: connect 5 s, first token 20 s.
  - Retry 429/5xx with jitter, only before the first streamed token.
  - Fallback chain per tier and a Redis circuit breaker.
  - Per-call token/cost accounting (price table in config). Tenant and global daily spend caps.
  - OTel GenAI semantic-convention spans.

### Channels & customer features
- **Widget:**
  - Loader script injects an iframe (isolated from host CSS/JS, simpler CSP) and authenticates with a public widget key plus origin allowlist.
  - Anonymous visitor token, Cloudflare Turnstile, rate limits.
  - Theming, EN/繁/简 UI, lead-capture form, "talk to a human".
  - Markdown rendered with sanitisation.
- **WhatsApp Cloud API:**
  - Webhook verify token + `X-Hub-Signature-256` check.
  - Dedupe by message id, then enqueue.
  - Respect the 24-hour window; short-link citations; non-text media gets a polite text-only reply.
  - Business-scoped by design (policy compliant).
- **Handoff inbox (admin):**
  - Live conversation list (SSE).
  - Take over (bot paused) → agent replies go out via web or WhatsApp → return to bot.
  - Email/Slack-webhook notifications.

### Admin console (`apps/web`)
- **Sources/documents:** status, re-index, visibility, ACL editor, **chunk inspector**.
- **Playground with retrieval trace view:** dense/BM25/RRF/rerank ranks side by side.
- **Source viewer:** pdf.js page with bbox highlight.
- **Analytics:**
  - Volume; answered/refused/handoff rates; feedback; TTFT p50/p95; cost per conversation.
  - **Topic clusters** (question embeddings → k-means/HDBSCAN → LLM labels; D3 view).
  - **Content gaps:** refused or low-confidence clusters → "Draft FAQ" (LLM draft → admin edits and approves → ingested as a document). This closes the feedback loop.
  - **Weekly AI insight email.**
- **Settings:** branding, languages, model profile, thresholds, retention, widget keys, WhatsApp connection, members/roles/groups.
- **Evals page:** runs over time, per-metric trends, per-case diffs.

### Evals (`harbor/evals`, CLI `harbor-eval run --dataset --profile`)
- **Dataset `hk-sme-v1`:** ~150 human-verified cases.
  - Languages: EN / zh-Hant / zh-Hans / **Cantonese colloquial** (e.g., 請病假要唔要醫生紙？).
  - Types: factoid, multi-hop, **unanswerable**, multi-turn follow-up, **ACL probe** (role-scoped), **prompt-injection** (a document with planted instructions), out-of-scope.
  - Candidates are LLM-generated, then Holden verifies and edits every case.
- **Deterministic metrics:** recall@k, MRR, nDCG (labelled chunks); **ACL leakage = 0** (restricted chunks in any candidate list); **cross-tenant leakage = 0**; script/language match; refusal accuracy; latency; cost.
- **LLM-judge metrics** (judge from a different model family than the generator): faithfulness (claims supported by cited context), answer correctness vs. reference, citation precision/recall, injection resistance.
- **Judge calibration:** Holden labels 50 answers. Report Cohen's κ in `docs/eval-report.md`.
- **CI:**
  - Every PR: retrieval-only eval (cached query embeddings, near-zero cost).
  - PRs labelled `run-evals`: 30-case generation smoke test.
  - Nightly on main: full eval.
  - `evals/thresholds.yaml` gates regressions. Results are posted as a PR comment and stored in the DB.
- **Published experiments (with numbers):**
  - Dense vs. BM25 vs. hybrid vs. hybrid+rerank.
  - Chunking variants; contextual headers on/off.
  - OpenAI vs. Qwen embeddings.
  - Model profiles (OpenAI/Azure, Anthropic, Qwen, DeepSeek) on Chinese/Cantonese quality × cost × latency. This is a highly market-relevant chart.
  - Agentic vs. single-shot on multi-hop questions.

### Security & compliance
- RLS + tenant-scoped repositories; Better Auth RBAC (`owner|admin|editor|member`); audit log.
- **Prompt-injection defences:** delimiting, instruction hierarchy, no tools in generation, link/image allowlist, eval suite. Mapped in `docs/owasp-llm-top10.md`.
- **PII:** optional ingestion redaction (HKID/phone/email patterns); contact numbers hashed and encrypted (app-level Fernet); per-tenant retention job; export/delete endpoints.
- `docs/pdpo-data-handling.md` maps features to PDPO and the PCPD AI/Agentic AI frameworks. It doubles as the client-facing one-pager.
- Secrets via SOPS+age; k3s secrets encryption; Trivy, pip-audit, pnpm audit, CodeQL, Renovate; security headers.

### Deployment
- **Helm chart `deploy/helm/harbor`:**
  - api (2 replicas; readiness/liveness; preStop drain), worker-default, worker-ingest-heavy (CPU/memory limits), web, widget.
  - `alembic upgrade` pre-upgrade Job.
  - K8s CronJobs: sync, analytics, retention, nightly evals, backup verification.
  - HPA manifests.
- **Cluster add-ons:** CloudNativePG (ParadeDB image, scheduled backups → R2, **documented restore drill**), Valkey StatefulSet, Traefik (k3s default) + cert-manager, Alloy.
- **GitHub Actions:**
  - `ci.yml`: ruff, mypy, pytest with Testcontainers (ParadeDB, Valkey), vitest, Playwright E2E against compose + mock-llm, Trivy.
  - `deploy.yml`: build → GHCR → `helm upgrade` to **staging on main**, **prod on tag** with environment approval + post-deploy smoke test.
- `deploy/compose`: dev stack + a single-VM client install profile.

---

## Milestones (~22 h/week → 8 weeks + setup)

| # | When | Deliverables | Exit criteria |
|---|---|---|---|
| **M0** Setup & spikes | Week 0 (~15 h) | Monorepo scaffold, `CLAUDE.md`, ADR template, compose dev stack + mock-llm, CI skeleton. VPS + k3s + Cloudflare + R2 + Grafana Cloud + Sentry. **Spikes:** (a) Better Auth org+JWT ↔ FastAPI JWKS; (b) pg_search Jieba on mixed EN/繁 text + filtered HNSW recall; (c) Docling timing/quality on 10 bilingual HK PDFs on CPU; (d) **verify reuse terms** for HK gov demo docs; (e) Azure OpenAI + DashScope intl + DeepSeek + Anthropic keys working | "Hello" API + web deployed to staging via CI; spike results in ADR drafts |
| **M1** Ingestion + retrieval core | Weeks 1–2 | Upload connector, Docling pipeline, chunking, embeddings (2 providers), hybrid + RRF + rerank, retrieval traces, playground + chunk inspector, eval harness v0 + 60-case retrieval set | recall@5 / MRR measured for 4 retrieval variants; ADR-001 lexical, ADR-002 chunking, ADR-003 embeddings finalised with numbers |
| **M2** Staff chat MVP | Weeks 3–4 | Better Auth tenants/roles/groups, RLS, ACLs, SSE chat with citations + pdf.js highlight, answerability gate, feedback, 4 providers + Azure, fallback/breaker/spend caps, generation metrics + judge calibration, Helm prod deploy, demo tenant seeded | **Public MVP demo + 2-min video**; 0 ACL/cross-tenant leakage; faithfulness baseline recorded |
| **M3** Customer channels | Week 5 | Website crawler, widget (iframe + Turnstile + lead capture), WhatsApp Cloud API (test number), handoff inbox, FAQ answer cache, rate limits | E2E: widget question → cited answer → handoff → agent reply; WhatsApp round-trip on test number |
| **M4** Insights + Drive | Week 6 | Analytics dashboard, topic clustering, content gaps → FAQ-draft loop, weekly insight email, Google Drive incremental sync + permission mapping | Dashboard populated from seeded + real demo traffic; Drive permission change reflected in retrieval within 1 sync |
| **M5** Hardening | Week 7 | CI eval gates + nightly evals, k6 load tests (mock-llm; 10k and 100k chunks), chaos checks (kill provider → fallback; kill api pod mid-stream), backup restore drill, alerts, injection/leakage suites, PII + retention, `security.md`, PDPO doc, OWASP LLM mapping | Targets below met or gaps documented; restore drill < 30 min |
| **M6** Showcase & enablement | Week 8 | README results table, `eval-report.md`, user/admin guides EN + 繁中, demo videos **EN + 普通話**, case-study page copy, freelance one-pager, agentic retrieval mode *(if on track; otherwise first post-v1 item)*. Update `ai-projects-plan.md` §3 and `holden-wang-profile.md` §9 with real evidence and resume bullets | Launch post published; all numbers link to reproducible reports |

**v1 targets** (tune after the M1/M2 baselines; publish actuals, never these targets):
- Quality: recall@5 ≥ 0.85; faithfulness ≥ 0.90; citation precision ≥ 0.90; refusal accuracy ≥ 0.85; injection suite ≥ 95% pass.
- **Leakage (ACL and cross-tenant) = 0.**
- Performance: TTFT p95 < 3 s (hosted); retrieval p95 < 300 ms at 50k chunks; 50-page digital PDF ingested < 60 s.
- Tests: backend core-module coverage ≥ 80%.

**Cut order if behind:** agentic mode → Drive connector (keep upload + crawler) → weekly insight email → k6 at 100k chunks. **Never cut:** evals, ACL/RLS, the leakage suites, backups.

**Post-v1 backlog:** local/on-prem profile (Ollama/vLLM + bge-m3 + Qwen), GraphRAG-lite, MCP server (`harbor.search`), Slack/Teams bots, SharePoint/Confluence/Notion, SAML SSO, Cantonese voice notes (ASR), switch the LLM layer to Switchyard.

---

## Demo Tenant

- **"Harbor Demo Co."** is a clearly fictional HK SME. Its content:
  - **Public:** fictional website FAQ, services and pricing pages (crawled), used for the widget and WhatsApp.
  - **Internal:** fictional employee handbook + public HK gov guides (Labour Dept Employment Ordinance guide, MPFA, IRD), pending M0 licence check.
  - **Restricted:** fictional management-only docs, to demo ACLs.
- Guest access: anonymous widget and a read-only staff demo login with a strict daily quota, cheap tier models, and a global spend cap.

---

## Verification

- **Local E2E:** `make dev` (compose: ParadeDB, Valkey, api, workers, web, widget, mock-llm) → `make seed-demo` → upload a bilingual PDF → watch status → ask EN, 繁 and Cantonese questions in the web app → check streaming, citations, pdf highlight and the retrieval trace.
- **Tests:**
  - Backend: `uv run pytest` (unit + Testcontainers integration, incl. RLS cross-tenant and ACL leakage tests).
  - Frontend: `pnpm -r test` (vitest).
  - E2E: `pnpm e2e` (Playwright: login, upload, chat with citations, widget handoff, admin takeover).
- **Evals:** `uv run harbor-eval run --dataset hk-sme-v1 --profile hk`, compare against `evals/thresholds.yaml`, and check that the report is written to `evals/reports/`.
- **Channels:** embed the widget on `tools/widget-test.html` from an allowed origin *and* a disallowed one (expect rejection). Send WhatsApp test-number messages, including a duplicate webhook delivery (expect a single reply).
- **Resilience:** with `mock-llm` returning 500s for the primary provider, the answer comes from the fallback and the breaker metric flips. Delete an api pod mid-stream; the client reconnects cleanly. Run `k6 run deploy/k6/chat.js` and record p95.
- **Ops:** CI deploy to staging → smoke test → tag → prod. Grafana dashboards show TTFT, retrieval stages, queue depth and cost. A test alert fires. The CNPG restore drill into a scratch namespace succeeds.
- **Security:** injection and leakage eval suites pass. Trivy shows no critical issues. The widget can't read restricted documents (API test with a widget token).
