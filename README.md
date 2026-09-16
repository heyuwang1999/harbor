# Harbor

**Bilingual knowledge assistant for Hong Kong teams: cited answers from your documents in English, 繁體中文, 简体中文 and Cantonese.**

> 🚧 Early development: milestone **M0 (setup & spikes)**. The roadmap is in [`docs/specs/harbor-v1-plan.md`](docs/specs/harbor-v1-plan.md). Numbers on this page come from committed, reproducible scripts; nothing here is a projection.

## What it will do (v1)
- Answers staff and customer questions from company documents, with **citations to the exact page**, and says "not found" instead of guessing.
- **Channels:** web app, embeddable website widget, WhatsApp, with handoff to a human.
- **Permission-aware retrieval:** tenant isolation with Postgres Row-Level Security and document-level ACLs enforced inside the search query.
- **Hybrid search tuned for Chinese:** BM25 (Jieba on OpenCC-normalised text) + vector search + reranking.
- **Continuous evaluation** in CI (retrieval recall, faithfulness, citation precision, refusal accuracy, zero-leakage checks).
- **Admin insights:** what people ask, where the documents have gaps, answer quality and cost over time.
- **Model profiles for HK realities:** Azure OpenAI + Qwen by default for HK deployments; Anthropic, OpenAI and DeepSeek also supported.

## Findings so far
From [ADR-0001](docs/adr/0001-retrieval-storage-chinese-lexical-and-filtered-vector-search.md) (small spike; the full eval comes in M1):
- Normalising Traditional → Simplified before Jieba tokenisation raised BM25 hit@1 from **0.83 to 0.96** on 24 HK-style queries and removed all zero-result queries.
- With pgvector defaults, a tenant-filtered HNSW query returned on average **0.9 of 10 requested rows (recall@10 = 0.09)** with no error. Exact search over the filtered set gave **recall 1.00 at the same ~2 ms** latency, so that is Harbor's default.

## Quick start (local)
Requires Docker, [uv](https://docs.astral.sh/uv/), Node 22 and pnpm 10.

```bash
make install        # Python + Node deps
make infra          # ParadeDB, Valkey, mock-llm
make dev-api        # http://localhost:8000/docs
make dev-web        # http://localhost:3000
make check          # lint, typecheck, tests
```

## Repository
```
apps/web          Next.js staff app & admin console
services/api      FastAPI backend (harbor_api)
tools/mock-llm    OpenAI-compatible fake provider for tests & load tests
deploy/compose    local stack
docs/adr          architecture decisions with evidence
spikes/           experiments behind the ADRs
```

## License
Apache-2.0
