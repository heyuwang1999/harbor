"""The one path that turns markdown into indexed chunks.

Both the demo seed and uploaded files go through here, so a change to chunking, embedding
or the denormalised access columns cannot apply to one and not the other.

Chunks are replaced inside the caller's transaction: a document is never half-indexed, and
a re-index either fully succeeds or leaves the previous version serving queries.
"""

import uuid
from dataclasses import dataclass

import structlog
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from harbor_api.ingestion.chunking import chunk_markdown
from harbor_api.llm.client import EmbeddingClient
from harbor_api.models import Chunk
from harbor_api.models.content import Visibility

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class DocumentRef:
    id: uuid.UUID
    tenant_id: uuid.UUID
    title: str
    visibility: Visibility


async def index_markdown(
    session: AsyncSession,
    *,
    document: DocumentRef,
    markdown: str,
    principals: list[uuid.UUID],
    embedder: EmbeddingClient,
) -> int:
    """Replace a document's chunks. Returns how many were written."""
    await session.execute(delete(Chunk).where(Chunk.document_id == document.id))

    pieces = chunk_markdown(markdown, title=document.title)
    if not pieces:
        return 0

    vectors = await embedder.embed([piece.embed_text for piece in pieces])
    for piece, vector in zip(pieces, vectors, strict=True):
        session.add(
            Chunk(
                tenant_id=document.tenant_id,
                document_id=document.id,
                ordinal=piece.ordinal,
                heading_path=piece.heading_path,
                text=piece.text,
                text_norm=piece.index_text,
                token_count=piece.token_count,
                embedding=vector,
                embedding_model=embedder.model,
                visibility=document.visibility,
                allowed_principals=principals,
            )
        )
    log.info(
        "ingestion.indexed",
        document_id=str(document.id),
        chunks=len(pieces),
        embedding_model=embedder.model,
    )
    return len(pieces)
