"""Ingest an uploaded document: storage → parse → chunk → embed → index.

Runs in the Celery worker, and inline in tests. Failures are recorded on the document with
a message the uploader can act on, because "failed" with no reason is the single most
annoying thing an ingestion pipeline can do.
"""

import uuid

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from harbor_api.core.config import Settings
from harbor_api.db.session import tenant_session
from harbor_api.ingestion import parsers
from harbor_api.ingestion.pipeline import DocumentRef, index_markdown
from harbor_api.llm.registry import embedding_client
from harbor_api.models.content import DocumentStatus, Visibility
from harbor_api.storage import LocalDiskStorage, Storage

log = structlog.get_logger(__name__)

# Which groups may read a document, by visibility. Restricted documents list their groups
# explicitly in document_acl; the seed applies the same rule.
VISIBILITY_PRINCIPAL_KEYS: dict[Visibility, tuple[str, ...]] = {
    Visibility.PUBLIC: ("everyone",),
    Visibility.INTERNAL: ("staff",),
}


def storage_for(settings: Settings) -> Storage:
    return LocalDiskStorage(settings.storage_root)


async def _set_status(
    session: AsyncSession,
    document_id: uuid.UUID,
    status: DocumentStatus,
    *,
    error: str | None = None,
) -> None:
    await session.execute(
        # jsonb_set returns NULL if any argument is NULL, which would blank the whole
        # metadata column on success; clearing the key is what "no error" means.
        text(
            "UPDATE documents SET status = :status, metadata = CASE "
            "  WHEN CAST(:error AS text) IS NULL THEN metadata - 'error' "
            "  ELSE jsonb_set(metadata, '{error}', to_jsonb(CAST(:error AS text)), true) "
            "END, updated_at = now() WHERE id = CAST(:id AS uuid)"
        ),
        {"status": status.value, "error": error, "id": str(document_id)},
    )


async def ingest_document(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    tenant_id: uuid.UUID,
    document_id: uuid.UUID,
    settings: Settings,
) -> int:
    """Parse and index one stored document. Returns the chunk count (0 on failure)."""
    storage = storage_for(settings)
    embedder = embedding_client(settings)

    async with tenant_session(sessionmaker, tenant_id) as session:
        row = (
            await session.execute(
                text(
                    "SELECT id, title, visibility, storage_key, metadata FROM documents "
                    "WHERE id = CAST(:id AS uuid)"
                ),
                {"id": str(document_id)},
            )
        ).one_or_none()
        if row is None:
            log.warning("ingestion.document_missing", document_id=str(document_id))
            return 0

        await _set_status(session, document_id, DocumentStatus.PARSING)
        filename = (row.metadata or {}).get("filename", "document")

        try:
            data = storage.get(row.storage_key)
            parsed = parsers.parse(data, filename)
        except parsers.UnparsableDocument as error:
            await _set_status(session, document_id, DocumentStatus.FAILED, error=str(error))
            log.info("ingestion.unparsable", document_id=str(document_id), reason=str(error))
            return 0
        except FileNotFoundError:
            await _set_status(
                session, document_id, DocumentStatus.FAILED, error="The stored file is missing."
            )
            return 0

        principals = await _principals_for(
            session, tenant_id, document_id, Visibility(row.visibility)
        )
        chunks = await index_markdown(
            session,
            document=DocumentRef(
                id=document_id,
                tenant_id=tenant_id,
                title=row.title,
                visibility=Visibility(row.visibility),
            ),
            markdown=parsed.markdown,
            principals=principals,
            embedder=embedder,
        )
        if chunks == 0:
            await _set_status(
                session,
                document_id,
                DocumentStatus.FAILED,
                error="The document produced no indexable text.",
            )
            return 0

        await _set_status(session, document_id, DocumentStatus.INDEXED)
        return chunks


async def _principals_for(
    session: AsyncSession, tenant_id: uuid.UUID, document_id: uuid.UUID, visibility: Visibility
) -> list[uuid.UUID]:
    if visibility is Visibility.RESTRICTED:
        rows = (
            await session.execute(
                text("SELECT group_id FROM document_acl WHERE document_id = CAST(:id AS uuid)"),
                {"id": str(document_id)},
            )
        ).all()
        return [row.group_id for row in rows]

    keys = VISIBILITY_PRINCIPAL_KEYS[visibility]
    rows = (
        await session.execute(
            text(
                "SELECT id FROM groups WHERE tenant_id = CAST(:tenant_id AS uuid) "
                "AND key = ANY(CAST(:keys AS text[]))"
            ),
            {"tenant_id": str(tenant_id), "keys": list(keys)},
        )
    ).all()
    return [row.id for row in rows]
