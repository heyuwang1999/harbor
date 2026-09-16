# ADR-0001: Retrieval storage: ParadeDB BM25 (Jieba on OpenCC-normalised text) + pgvector with filter-aware search

- **Status:** accepted for M1 (revisit with the real `hk-sme-v1` eval set at the end of M1)
- **Date:** 2026-09-15
- **Evidence:** [`spikes/0001-lexical-search/run.py`](../../spikes/0001-lexical-search/run.py) → [`RESULTS.md`](../../spikes/0001-lexical-search/RESULTS.md)

## Context
Harbor answers questions in English, Traditional Chinese (HK), Simplified Chinese and written Cantonese over the same documents. It needs:

1. **Lexical (BM25) retrieval that works for Chinese.** Stock Postgres full-text search has no Chinese word segmentation.
2. **Dense retrieval that stays correct under tenant/ACL filters.** Every query is filtered by `tenant_id`, visibility and allowed principals, and a missed chunk is a wrong or refused answer.
3. **One operational datastore.** The deployment is a single VPS running k3s, so avoiding a separate search cluster matters.

Options considered for lexical search: `zhparser`/`pg_jieba` (extra C extensions, no BM25 ranking), bge-m3 sparse vectors (ties lexical search to one embedding model), ParadeDB `pg_search` (Tantivy BM25 inside Postgres, with a Jieba tokenizer).

## Decision
1. **Use the ParadeDB image** (`paradedb/paradedb:0.25.9-pg18`, which bundles `pg_search` 0.25.9 and `pgvector` 0.8.4) as the only database.
2. **Index BM25 on a normalised column:** `chunks.text_norm = OpenCC hk2s(text)`, tokenised with `::pdb.jieba`. Apply the same `hk2s` conversion to queries. Keep the original `text` for display and citations.
3. **Put tenant/visibility/principal filters inside the BM25 query.** pg_search pushes them into the Tantivy query (verified with `EXPLAIN`: `TopKScanExecState` with a `term` filter).
4. **Filter-aware dense search:**
   - **Default: exact search over the filtered set.** Filter first (materialised CTE on `tenant_id` + ACL), then order by cosine distance. SME tenants will have well under ~50k chunks, where this is both exact and fast.
   - **Large filtered sets** (threshold to be tuned in the M5 load test at 50k/100k chunks): HNSW with `hnsw.iterative_scan = relaxed_order`, `hnsw.ef_search = 400`, then re-sort by distance in the outer query.
   - **Never** run filtered HNSW with iterative scans off.
5. **Add an HK glossary / query-expansion step** for abbreviations and colloquialisms that neither tokenizer nor normalisation can bridge (MPF↔強積金, OT↔加班, 黑雨↔黑色暴雨, 醫生紙↔醫生證明書). Dense retrieval in the hybrid pipeline is the main backstop.
6. **Compose sets `shm_size: 1g`** for Postgres, and the Helm/CNPG config must also give Postgres enough `/dev/shm`.

## Evidence
**Part A: BM25 passage retrieval.** 16 fictional handbook passages, 24 queries (Cantonese, Traditional, Simplified, English, mixed).

| config | hit@1 | hit@3 | MRR | zero-result queries |
|---|---|---|---|---|
| raw text | 0.83 | 0.83 | 0.83 | 3 |
| raw + query punctuation cleanup | 0.83 | 0.83 | 0.83 | 3 |
| **hk2s-normalised** | **0.96** | **0.96** | **0.96** | **0** |
| hk2s + punctuation cleanup | 0.96 | 0.96 | 0.96 | 0 |

- Why normalisation helps: Jieba's dictionary is Simplified-centric. It split 员工/连续 correctly but produced a single token for Traditional 僱員連續.
- Punctuation cleanup had no effect, so it is not adopted.
- The remaining miss was `MPF 供幾多？` (an abbreviation gap), which motivated decision 5.

**Part B: filtered vector search.** 40k clustered vectors × 128d, 50 tenants (~2% selectivity per query, ~800 rows), 40 queries, k=10.

| mode | recall@10 | rows returned (of 10) | mean ms | p95 ms |
|---|---|---|---|---|
| HNSW, iterative scan off (pgvector default) | **0.090** | **0.9** | 0.3 | 0.5 |
| HNSW, strict_order | 0.780 | 10.0 | 2.1 | 3.5 |
| HNSW, relaxed_order | 0.873 | 10.0 | 1.9 | 3.2 |
| HNSW, strict_order, ef_search=100 | 0.818 | 10.0 | 2.0 | 3.2 |
| HNSW, relaxed_order, ef_search=400 | 0.920 | 10.0 | 2.3 | 3.4 |
| **exact: filter first, then sort** | **1.000** | 10.0 | 2.0 | 2.3 |
| planner's choice with btree(tenant) + HNSW | 0.780 | 10.0 | 2.1 | 3.4 |

- With the pgvector defaults, a filtered HNSW query **silently returns fewer rows than requested**. HNSW finds global neighbours and then the filter discards most of them.
- Even with a B-tree on `tenant`, the Postgres planner still chose HNSW. Harbor must therefore pick the strategy explicitly, not rely on the planner.

**Caveats:**
- Part A is a 24-query smoke test, not an eval. The real decision gate is `hk-sme-v1` (~150 cases) in M1.
- Part B uses synthetic Gaussian-mixture vectors, which are harder than real embeddings (higher intrinsic dimension). Absolute HNSW recall on real data will likely be higher, but the silent-truncation failure mode applies regardless.
- Latencies were measured on a laptop over localhost.

## Consequences
- **Easier:**
  - One database for relational data, BM25 and vectors.
  - Tenant/ACL filtering stays in SQL, where Row-Level Security also applies.
  - Backups cover everything.
- **Harder:**
  - Ingestion must maintain `text_norm`.
  - Retrieval code needs an explicit strategy switch (exact vs. HNSW) based on estimated filtered-set size. Keep a per-tenant `chunk_count` so the estimate is cheap.
  - Tokenizer behaviour is tied to ParadeDB versions, so the image is pinned and upgrades must re-run this spike.
- **Licensing:** `pg_search` is AGPL-3.0. Using it unmodified as a database server is standard practice, but note it in the client-facing data-handling doc and re-check before any managed/hosted offering.
- **Revisit:**
  - End of M1: rerun Part A on `hk-sme-v1`; compare Jieba vs. the `chinese_compatible` tokenizer and bge-m3 sparse vectors.
  - M5: set the exact/HNSW threshold from load tests at 50k and 100k chunks.
