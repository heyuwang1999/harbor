# Harbor

**Bilingual knowledge assistant for Hong Kong teams: cited answers from your documents in English, 繁體中文, 简体中文 and Cantonese.**

> 🚧 Early development: milestone **M1a — a working demo on the production schema**. The roadmap is in [`docs/specs/harbor-v1-plan.md`](docs/specs/harbor-v1-plan.md). Numbers on this page come from committed, reproducible scripts; nothing here is a projection.

## Try the demo (one command, no API keys)

```bash
make demo        # builds, migrates, seeds, then serves http://localhost:3000
make demo-down   # stop (ARGS=-v also drops the database volume)
```

The demo company is a fictional Hong Kong bakery chain with six bilingual documents:
public customer FAQs, an internal staff handbook and expense policy, and a management-only
salary band table. Answers run on a **mock model** that quotes the retrieved passages
verbatim, so the demo is free, offline and deterministic — while the retrieval, access
control, citation validation and persistence paths are the real ones.

Worth trying:

| Ask | What it shows |
|---|---|
| 請病假要唔要醫生紙？ | Colloquial Cantonese question answered from a Traditional Chinese handbook, with a citation you can open |
| MPF 供幾多？ · 八號風球使唔使返工？ | HK glossary expansion: neither "MPF" nor "風球" appears in the documents |
| 强积金供款比例是多少？ | Simplified query against Traditional sources (OpenCC normalisation) |
| 邊個發明咗小籠包？ | An honest refusal instead of a guess |
| 分店經理的月薪範圍是多少？ | Switch **Visitor → Staff → Management**: only management gets an answer, and restricted passages never appear in anyone else's retrieval trace |

The **retrieval trace** panel under each answer shows BM25 rank vs. vector rank vs. fused
rank, per-stage timings and token usage — the same rows the evals score in M1b.

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
apps/web               Next.js demo chat, citations, retrieval trace
packages/chat-client   framework-free SSE client shared by the app and the M3 widget
services/api           FastAPI backend (harbor_api): ingestion, retrieval, generation
tools/mock-llm         OpenAI-compatible fake provider for tests & load tests
deploy/compose         local stack and the one-command demo
docs/adr               architecture decisions with evidence
docs/specs             the chat stream contract and the v1 plan
spikes/                experiments behind the ADRs
```

## How an answer is produced

```
question → OpenCC hk2s normalise → HK glossary expansion
        → BM25 (Jieba) ‖ exact vector search, both filtered by tenant + visibility + principals IN SQL
        → reciprocal rank fusion → answerability gate → refuse, or
        → generate with [S#] citations → validate citations, strip links → persist answer + trace
```

Tenant isolation is enforced twice: every query filters by tenant, and PostgreSQL
Row-Level Security blocks anything that forgets. The app connects as a non-superuser role
so those policies actually apply, and the tests assert it as that role.

## License
Apache-2.0
