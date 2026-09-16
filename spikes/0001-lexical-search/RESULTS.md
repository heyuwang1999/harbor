# Spike 0001 results

Generated 2026-09-15 by `uv run spikes/0001-lexical-search/run.py` against `paradedb/paradedb:0.25.9-pg18` (pg_search 0.25.9, pgvector 0.8.4) on a local laptop. See docs/adr/0001.


## Part A: BM25 (Jieba) passage retrieval, 16 passages, 24 queries

| config | hit@1 | hit@3 | MRR | zero-result queries |
|---|---|---|---|---|
| raw | 0.83 | 0.83 | 0.83 | 3 |
| raw+clean | 0.83 | 0.83 | 0.83 | 3 |
| hk2s | 0.96 | 0.96 | 0.96 | 0 |
| hk2s+clean | 0.96 | 0.96 | 0.96 | 0 |

Misses (not in top 3) for hk2s+clean: ['MPF 供幾多？']

## Part B: filtered HNSW, 40000 vectors x 128d, 50 tenants (~2% selectivity)

HNSW build: 4.2s (m=16, ef_construction=64)

| mode | recall@10 | rows returned (of 10) | mean ms | p95 ms |
|---|---|---|---|---|
| hnsw, iterative_scan=off, ef_search=40 | 0.090 | 0.9 | 0.3 | 0.5 |
| hnsw, iterative_scan=strict_order | 0.780 | 10.0 | 2.1 | 3.5 |
| hnsw, iterative_scan=relaxed_order | 0.873 | 10.0 | 1.9 | 3.2 |
| hnsw, strict_order, ef_search=100 | 0.818 | 10.0 | 2.0 | 3.2 |
| hnsw, relaxed_order, ef_search=400 | 0.920 | 10.0 | 2.3 | 3.4 |
| exact: filter first, then sort (hnsw disabled) | 1.000 | 10.0 | 2.0 | 2.3 |
| planner's choice with btree(tenant) + hnsw | 0.780 | 10.0 | 2.1 | 3.4 |

Plan with btree present: Limit  (cost=257.27..618.35 rows=10 width=12) / InitPlan 1 / ->  Limit  (cost=0.00..0.08 rows=1 width=520)
