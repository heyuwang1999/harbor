# /// script
# requires-python = ">=3.12"
# dependencies = ["psycopg[binary]>=3.2", "numpy>=2", "opencc>=1.1"]
# ///
"""Spike for ADR-0001: Chinese lexical search + filtered vector search in ParadeDB.

Part A: does BM25 (pg_search, Jieba) retrieve the right passage for HK-style questions,
        raw vs. OpenCC hk2s-normalised text, with/without query punctuation cleanup?
Part B: does pgvector HNSW keep recall under a selective tenant filter, and do
        iterative index scans (pgvector >= 0.8) fix it?

Run against the compose stack:  uv run spikes/0001-lexical-search/run.py
"""

import os
import re
import statistics
import time

import numpy as np
import opencc
import psycopg

DSN = os.environ.get("SPIKE_DSN", "postgresql://harbor:harbor@127.0.0.1:5432/harbor")
to_simplified = opencc.OpenCC("hk2s.json")

# Fictional "Harbor Demo Co." staff handbook passages (original text, not copied from any source).
CORPUS: dict[int, str] = {
    1: "僱員連續工作滿四星期，每星期工作最少十八小時，即可享有法定假日。",
    2: "申請病假津貼時，僱員須提交註冊醫生簽發的醫生證明書。",
    3: "僱員每服務滿十二個月，可享有有薪年假，首兩年每年七天。",
    4: "Staff may claim up to HK$500 per month for mobile phone expenses with receipts.",
    5: "加班工作須事先獲部門主管批准，補假須於三個月內放取。",
    6: "公司為所有全職僱員提供團體醫療保險，涵蓋門診及住院。",
    7: "强积金供款：雇主及雇员各按有关入息的百分之五供款。",
    8: "Remote work is allowed up to two days per week subject to manager approval.",
    9: "產假為十四星期，合資格僱員可獲發產假薪酬。",
    10: "侍產假為五天，須於嬰兒出生前後指定期間內放取。",
    11: "如遇八號或以上颱風信號或黑色暴雨警告，非必要員工毋須上班。",
    12: "報銷交通費須於一個月內經系統提交，並附上收據。",
    13: "Probation period is three months; notice period during probation is seven days.",
    14: "試用期滿後，任何一方終止僱傭合約須給予一個月通知。",
    15: "員工培訓資助每年上限為港幣八千元，須完成課程並取得證書。",
    16: "辦公室開放時間為星期一至五上午九時至下午六時。",
}

# (query, expected passage id, style)
QUERIES: list[tuple[str, int, str]] = [
    ("法定假日要做滿幾耐先有？", 1, "cantonese"),
    ("法定假日的资格要求", 1, "simplified"),
    ("請病假要唔要醫生紙？", 2, "cantonese"),
    ("病假津貼需要什麼證明", 2, "traditional"),
    ("有薪年假有幾多日？", 3, "cantonese"),
    ("年假天数", 3, "simplified"),
    ("How much can I claim for my phone bill?", 4, "english"),
    ("mobile phone expenses", 4, "english"),
    ("OT要邊個批准？", 5, "cantonese-mixed"),
    ("加班補假幾時要放？", 5, "cantonese"),
    ("公司有冇醫療保險？", 6, "cantonese"),
    ("強積金供款比例", 7, "traditional"),
    ("MPF 供幾多？", 7, "cantonese-mixed"),
    ("Can I work from home?", 8, "english"),
    ("remote work days per week", 8, "english"),
    ("產假有幾多個星期？", 9, "cantonese"),
    ("侍产假几天", 10, "simplified"),
    ("八號風球使唔使返工？", 11, "cantonese"),
    ("黑雨要上班嗎", 11, "traditional"),
    ("交通費點報銷？", 12, "cantonese"),
    ("probation notice period", 13, "english"),
    ("辭職要俾幾耐通知？", 14, "cantonese"),
    ("培訓資助上限", 15, "traditional"),
    ("辦公時間", 16, "traditional"),
]

PUNCT = re.compile(r"[\s!-/:-@\[-`{-~\u3000-\u303f\uff00-\uff0f\uff1a-\uff20]+")


def clean(query: str) -> str:
    return PUNCT.sub(" ", query).strip()


def lexical(conn: psycopg.Connection) -> None:
    conn.execute("DROP TABLE IF EXISTS spike_chunks")
    conn.execute("CREATE TABLE spike_chunks (id int PRIMARY KEY, body text, body_norm text)")
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO spike_chunks VALUES (%s, %s, %s)",
            [(i, t, to_simplified.convert(t)) for i, t in CORPUS.items()],
        )
    conn.execute(
        "CREATE INDEX spike_chunks_bm25 ON spike_chunks USING bm25 "
        "(id, (body::pdb.jieba), (body_norm::pdb.jieba)) WITH (key_field='id')"
    )

    configs = {
        "raw": ("body", lambda q: q),
        "raw+clean": ("body", clean),
        "hk2s": ("body_norm", to_simplified.convert),
        "hk2s+clean": ("body_norm", lambda q: clean(to_simplified.convert(q))),
    }
    print("\n## Part A: BM25 (Jieba) passage retrieval, 16 passages, 24 queries\n")
    print("| config | hit@1 | hit@3 | MRR | zero-result queries |")
    print("|---|---|---|---|---|")
    misses: dict[str, list[str]] = {}
    for name, (column, transform) in configs.items():
        ranks: list[int | None] = []
        for query, expected, _style in QUERIES:
            q = transform(query)
            rows = conn.execute(
                f"SELECT id FROM spike_chunks WHERE {column} ||| %s "  # noqa: S608 - fixed column names
                "ORDER BY pdb.score(id) DESC LIMIT 3",
                (q,),
            ).fetchall()
            ids = [r[0] for r in rows]
            ranks.append(ids.index(expected) + 1 if expected in ids else None)
            if expected not in ids:
                misses.setdefault(name, []).append(query)
        hit1 = sum(r == 1 for r in ranks) / len(ranks)
        hit3 = sum(r is not None for r in ranks) / len(ranks)
        mrr = sum(1 / r for r in ranks if r) / len(ranks)
        zero = sum(
            1
            for query, _, _ in QUERIES
            if not conn.execute(
                f"SELECT 1 FROM spike_chunks WHERE {column} ||| %s LIMIT 1",  # noqa: S608
                (transform(query),),
            ).fetchone()
        )
        print(f"| {name} | {hit1:.2f} | {hit3:.2f} | {mrr:.2f} | {zero} |")
    print("\nMisses (not in top 3) for hk2s+clean:", misses.get("hk2s+clean", []))
    conn.execute("DROP TABLE spike_chunks")


def vector(conn: psycopg.Connection) -> None:
    rng = np.random.default_rng(7)
    n, dim, tenants, n_queries, k = 40_000, 128, 50, 40, 10
    centroids = rng.normal(size=(200, dim))
    data = centroids[rng.integers(0, 200, n)] + rng.normal(scale=0.6, size=(n, dim))
    data /= np.linalg.norm(data, axis=1, keepdims=True)
    tenant_of = rng.integers(0, tenants, n)

    conn.execute("DROP TABLE IF EXISTS spike_vecs")
    conn.execute(f"CREATE TABLE spike_vecs (id int PRIMARY KEY, tenant int, embedding vector({dim}))")
    with conn.cursor().copy("COPY spike_vecs (id, tenant, embedding) FROM STDIN") as copy:
        for i in range(n):
            copy.write_row((i, int(tenant_of[i]), "[" + ",".join(f"{x:.6f}" for x in data[i]) + "]"))
    conn.execute("SET maintenance_work_mem = '256MB'")
    # Compose now sets shm_size; this keeps the spike runnable on stacks started without it.
    conn.execute("SET max_parallel_maintenance_workers = 0")
    started = time.perf_counter()
    conn.execute("CREATE INDEX ON spike_vecs USING hnsw (embedding vector_cosine_ops)")
    build_s = time.perf_counter() - started
    conn.execute("ANALYZE spike_vecs")

    queries = centroids[rng.integers(0, 200, n_queries)] + rng.normal(scale=0.6, size=(n_queries, dim))
    queries /= np.linalg.norm(queries, axis=1, keepdims=True)
    query_tenants = rng.integers(0, tenants, n_queries)

    def truth(qi: int) -> set[int]:
        mask = np.where(tenant_of == query_tenants[qi])[0]
        sims = data[mask] @ queries[qi]
        return set(mask[np.argsort(-sims)[:k]].tolist())

    def run(label: str, settings: list[str]) -> None:
        conn.execute("RESET ALL")
        for s in settings:
            conn.execute(s)
        recalls, latencies, returned = [], [], []
        for qi in range(n_queries):
            vec = "[" + ",".join(f"{x:.6f}" for x in queries[qi]) + "]"
            started = time.perf_counter()
            rows = conn.execute(
                "SELECT id FROM spike_vecs WHERE tenant = %s ORDER BY embedding <=> %s::vector LIMIT %s",
                (int(query_tenants[qi]), vec, k),
            ).fetchall()
            latencies.append((time.perf_counter() - started) * 1000)
            ids = {r[0] for r in rows}
            returned.append(len(ids))
            recalls.append(len(ids & truth(qi)) / k)
        p95 = statistics.quantiles(latencies, n=20)[18]
        print(
            f"| {label} | {statistics.mean(recalls):.3f} | {statistics.mean(returned):.1f} "
            f"| {statistics.mean(latencies):.1f} | {p95:.1f} |"
        )

    print(f"\n## Part B: filtered HNSW, {n} vectors x {dim}d, {tenants} tenants (~2% selectivity)\n")
    print(f"HNSW build: {build_s:.1f}s (m=16, ef_construction=64)\n")
    print("| mode | recall@10 | rows returned (of 10) | mean ms | p95 ms |")
    print("|---|---|---|---|---|")
    run("hnsw, iterative_scan=off, ef_search=40", ["SET hnsw.iterative_scan = off"])
    run("hnsw, iterative_scan=strict_order", ["SET hnsw.iterative_scan = strict_order"])
    run("hnsw, iterative_scan=relaxed_order", ["SET hnsw.iterative_scan = relaxed_order"])
    run(
        "hnsw, strict_order, ef_search=100",
        ["SET hnsw.iterative_scan = strict_order", "SET hnsw.ef_search = 100"],
    )
    run(
        "hnsw, relaxed_order, ef_search=400",
        ["SET hnsw.iterative_scan = relaxed_order", "SET hnsw.ef_search = 400"],
    )
    run(
        "exact: filter first, then sort (hnsw disabled)",
        ["SET enable_indexscan = off"],
    )
    conn.execute("CREATE INDEX ON spike_vecs (tenant)")
    conn.execute("ANALYZE spike_vecs")
    run("planner's choice with btree(tenant) + hnsw", ["SET hnsw.iterative_scan = strict_order"])
    plan = conn.execute(
        "EXPLAIN SELECT id FROM spike_vecs WHERE tenant = 3 "
        "ORDER BY embedding <=> (SELECT embedding FROM spike_vecs LIMIT 1) LIMIT 10"
    ).fetchall()
    print("\nPlan with btree present:", " / ".join(r[0].strip() for r in plan[:3]))
    conn.execute("DROP TABLE spike_vecs")


if __name__ == "__main__":
    with psycopg.connect(DSN, autocommit=True) as conn:
        lexical(conn)
        vector(conn)
