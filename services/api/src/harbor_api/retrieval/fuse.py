from collections.abc import Sequence

from harbor_api.retrieval.types import Candidate


def reciprocal_rank_fusion(lists: Sequence[Sequence[Candidate]], *, k: int = 60) -> list[Candidate]:
    """Merge ranked lists with RRF: score = Σ 1/(k + rank).

    Rank-based fusion avoids comparing a BM25 score against a cosine similarity, which
    are on unrelated scales. k=60 is the value from the original paper and the common
    default; it is compared against alternatives on the eval set in M1b.
    """
    merged: dict[str, Candidate] = {}
    for ranked in lists:
        for rank, candidate in enumerate(ranked, start=1):
            key = str(candidate.chunk_id)
            existing = merged.get(key)
            if existing is None:
                merged[key] = existing = candidate
            else:
                # Same chunk from the other retriever: keep both sets of evidence.
                for attribute in ("bm25_rank", "bm25_score", "vector_rank", "similarity"):
                    if getattr(existing, attribute) is None:
                        setattr(existing, attribute, getattr(candidate, attribute))
            existing.fused_score += 1.0 / (k + rank)
    return sorted(merged.values(), key=lambda c: c.fused_score, reverse=True)
