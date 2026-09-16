import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Candidate:
    """A retrieved passage with the evidence for why it was retrieved.

    The per-stage ranks are what the demo's trace panel renders and what the retrieval
    eval scores, so they are carried all the way through rather than recomputed.
    """

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    ordinal: int
    text: str
    heading_path: list[str]
    visibility: str
    document_title: str = ""
    bm25_rank: int | None = None
    bm25_score: float | None = None
    vector_rank: int | None = None
    similarity: float | None = None
    fused_score: float = 0.0
    token_count: int = 0

    def as_trace(self) -> dict[str, Any]:
        return {
            "chunk_id": str(self.chunk_id),
            "document_id": str(self.document_id),
            "document_title": self.document_title,
            "heading_path": self.heading_path,
            "visibility": self.visibility,
            "bm25_rank": self.bm25_rank,
            "bm25_score": self.bm25_score,
            "vector_rank": self.vector_rank,
            "similarity": self.similarity,
            "fused_score": round(self.fused_score, 6),
        }


@dataclass
class RetrievalResult:
    query: str
    normalized_query: str
    candidates: list[Candidate] = field(default_factory=list)
    context: list[Candidate] = field(default_factory=list)
    answerable: bool = True
    strategy: dict[str, Any] = field(default_factory=dict)
    timings_ms: dict[str, float] = field(default_factory=dict)
