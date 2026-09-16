"""Hybrid retrieval: BM25 and vector search under the same access filter, fused with RRF.

ADR-0001 fixes two things this module implements:
  - queries are normalised with OpenCC before hitting the Jieba-tokenised BM25 index;
  - filtered vector search is *exact by default*. Filtered HNSW silently returned 0.9 of
    10 requested rows in the spike, which for permission-filtered retrieval means quietly
    losing the answer. Only above `exact_search_max_chunks` does this switch to HNSW with
    iterative scans.
"""

import time
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from harbor_api.core.config import Settings
from harbor_api.core.text import count_tokens, normalize
from harbor_api.llm.client import EmbeddingClient
from harbor_api.retrieval.access import AccessScope
from harbor_api.retrieval.fuse import reciprocal_rank_fusion
from harbor_api.retrieval.glossary import expand
from harbor_api.retrieval.types import Candidate, RetrievalResult

_SELECT = "c.id, c.document_id, c.ordinal, c.text, c.heading_path, c.visibility, c.token_count"


def _row_to_candidate(row: Any) -> Candidate:
    return Candidate(
        chunk_id=row.id,
        document_id=row.document_id,
        ordinal=row.ordinal,
        text=row.text,
        heading_path=list(row.heading_path or []),
        visibility=str(row.visibility),
        token_count=row.token_count,
    )


async def bm25_search(
    session: AsyncSession, scope: AccessScope, query: str, limit: int
) -> list[Candidate]:
    statement = text(
        f"SELECT {_SELECT}, pdb.score(c.id) AS score FROM chunks c "  # noqa: S608 - fixed identifiers
        f"WHERE c.text_norm ||| :query AND {scope.sql_filter} "
        "ORDER BY pdb.score(c.id) DESC LIMIT :limit"
    )
    rows = (
        await session.execute(statement, {**scope.params, "query": query, "limit": limit})
    ).all()
    candidates = []
    for rank, row in enumerate(rows, start=1):
        candidate = _row_to_candidate(row)
        candidate.bm25_rank = rank
        candidate.bm25_score = float(row.score)
        candidates.append(candidate)
    return candidates


async def vector_search(
    session: AsyncSession,
    scope: AccessScope,
    embedding: list[float],
    limit: int,
    *,
    exact: bool,
) -> list[Candidate]:
    vector_literal = "[" + ",".join(f"{value:.6f}" for value in embedding) + "]"
    if exact:
        # MATERIALIZED forces the filter to run first, then an exact sort over what is left.
        statement = text(
            "WITH scoped AS MATERIALIZED ("  # noqa: S608 - fixed identifiers, bound params
            f"  SELECT {_SELECT}, c.embedding FROM chunks c "
            f"  WHERE c.embedding IS NOT NULL AND {scope.sql_filter}"
            ") "
            "SELECT id, document_id, ordinal, text, heading_path, visibility, token_count, "
            "       1 - (embedding <=> CAST(:vector AS vector)) AS similarity "
            "FROM scoped ORDER BY embedding <=> CAST(:vector AS vector) LIMIT :limit"
        )
    else:
        await session.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
        await session.execute(text("SET LOCAL hnsw.ef_search = 400"))
        statement = text(
            f"SELECT {_SELECT}, "  # noqa: S608 - fixed identifiers
            "1 - (c.embedding <=> CAST(:vector AS vector)) AS similarity FROM chunks c "
            f"WHERE c.embedding IS NOT NULL AND {scope.sql_filter} "
            "ORDER BY c.embedding <=> CAST(:vector AS vector) LIMIT :limit"
        )
    rows = (
        await session.execute(statement, {**scope.params, "vector": vector_literal, "limit": limit})
    ).all()
    candidates = []
    for rank, row in enumerate(rows, start=1):
        candidate = _row_to_candidate(row)
        candidate.vector_rank = rank
        candidate.similarity = float(row.similarity)
        candidates.append(candidate)
    return candidates


async def _tenant_chunk_count(session: AsyncSession, tenant_id: uuid.UUID) -> int:
    result = await session.execute(
        text("SELECT count(*) FROM chunks WHERE tenant_id = CAST(:tenant_id AS uuid)"),
        {"tenant_id": str(tenant_id)},
    )
    return int(result.scalar_one())


async def _attach_titles(session: AsyncSession, candidates: list[Candidate]) -> None:
    if not candidates:
        return
    ids = list({candidate.document_id for candidate in candidates})
    rows = (
        await session.execute(
            text("SELECT id, title FROM documents WHERE id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": [str(document_id) for document_id in ids]},
        )
    ).all()
    titles = {row.id: row.title for row in rows}
    for candidate in candidates:
        candidate.document_title = titles.get(candidate.document_id, "")


def _build_context(candidates: list[Candidate], settings: Settings) -> list[Candidate]:
    """Keep only candidates with real evidence, then pack to the token budget.

    A candidate that only appeared in the vector list with a near-zero similarity is
    noise — which is exactly what the deterministic mock embeddings produce, so the demo
    leans on BM25 and never pads the prompt with unrelated passages.
    """
    context: list[Candidate] = []
    budget = settings.context_token_budget
    best_bm25 = max((candidate.bm25_score or 0) for candidate in candidates) if candidates else 0.0
    lexical_floor = max(settings.min_bm25_score, best_bm25 * settings.bm25_relative_floor)
    for candidate in candidates:
        has_lexical = (candidate.bm25_score or 0) >= lexical_floor
        has_semantic = (candidate.similarity or 0) >= settings.min_vector_similarity
        if not (has_lexical or has_semantic):
            continue
        tokens = candidate.token_count or count_tokens(candidate.text)
        if budget - tokens < 0:
            break
        budget -= tokens
        context.append(candidate)
        if len(context) >= settings.context_top_k:
            break
    return context


async def hybrid_search(
    session: AsyncSession,
    *,
    scope: AccessScope,
    query: str,
    settings: Settings,
    embedder: EmbeddingClient,
) -> RetrievalResult:
    normalized = normalize(query)
    # Everyday HK wording rarely matches formal document wording; expand before BM25.
    lexical_query, expanded_terms = expand(normalized)
    timings: dict[str, float] = {}

    started = time.perf_counter()
    embedding = (await embedder.embed([query]))[0]
    timings["embed"] = (time.perf_counter() - started) * 1000

    chunk_count = await _tenant_chunk_count(session, scope.tenant_id)
    exact = chunk_count <= settings.exact_search_max_chunks

    started = time.perf_counter()
    # asyncpg runs one statement at a time per connection, so these share the session
    # sequentially today; M1b moves them onto separate connections to overlap the work.
    lexical = await bm25_search(session, scope, lexical_query, settings.bm25_top_k)
    timings["bm25"] = (time.perf_counter() - started) * 1000

    started = time.perf_counter()
    semantic = await vector_search(session, scope, embedding, settings.vector_top_k, exact=exact)
    timings["vector"] = (time.perf_counter() - started) * 1000

    fused = reciprocal_rank_fusion([lexical, semantic], k=settings.rrf_k)
    await _attach_titles(session, fused[: settings.context_top_k * 3])
    context = _build_context(fused, settings)

    return RetrievalResult(
        query=query,
        normalized_query=normalized,
        candidates=fused[: settings.context_top_k * 3],
        context=context,
        answerable=bool(context),
        strategy={
            "vector_search": "exact" if exact else "hnsw_iterative",
            "tenant_chunks": chunk_count,
            "bm25_top_k": settings.bm25_top_k,
            "vector_top_k": settings.vector_top_k,
            "rrf_k": settings.rrf_k,
            "role": scope.role,
            "embedding_model": embedder.model,
            "expanded_terms": expanded_terms,
        },
        timings_ms={key: round(value, 2) for key, value in timings.items()},
    )
