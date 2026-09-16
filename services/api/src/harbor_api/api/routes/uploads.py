"""Upload, re-index and delete documents.

Uploading returns as soon as the file is stored and queued: parsing a large PDF and
embedding its chunks takes far longer than a request should. The client polls
`GET /v1/documents/{id}` for `pending → parsing → indexed | failed`.
"""

import uuid
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from sqlalchemy import text

from harbor_api.api.deps import ScopeDep, SessionmakerDep, SettingsDep, TenantDep
from harbor_api.db.session import tenant_session
from harbor_api.ingestion.parsers import MAX_UPLOAD_BYTES, UnparsableDocument, validate_upload
from harbor_api.ingestion.service import ingest_document
from harbor_api.models.content import DocumentStatus, SourceKind, Visibility
from harbor_api.storage import content_hash, storage_key
from harbor_api.workers.celery_app import ingest_document_task

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/v1", tags=["documents"])

# Visitors are anonymous customer-channel users; they must never add to the knowledge base.
UPLOAD_ROLES = {"staff", "management"}


def _require_upload_role(scope: ScopeDep) -> None:
    if scope.role not in UPLOAD_ROLES:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="this role cannot upload documents")


async def _upload_source_id(session: Any, tenant_id: uuid.UUID) -> uuid.UUID:
    """One 'Uploads' source per tenant, created on demand."""
    row = (
        await session.execute(
            text(
                "SELECT id FROM sources WHERE tenant_id = CAST(:t AS uuid) AND kind = 'upload' "
                "ORDER BY created_at LIMIT 1"
            ),
            {"t": str(tenant_id)},
        )
    ).one_or_none()
    if row is not None:
        return uuid.UUID(str(row.id))

    source_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO sources (id, tenant_id, kind, name, config) VALUES "
            "(CAST(:id AS uuid), CAST(:t AS uuid), :kind, 'Uploads', '{}'::jsonb)"
        ),
        {"id": str(source_id), "t": str(tenant_id), "kind": SourceKind.UPLOAD.value},
    )
    return source_id


@router.post("/documents", status_code=status.HTTP_202_ACCEPTED)
async def upload_document(
    scope: ScopeDep,
    tenant: TenantDep,
    settings: SettingsDep,
    sessionmaker: SessionmakerDep,
    file: Annotated[UploadFile, File()],
    visibility: Annotated[Visibility, Form()] = Visibility.INTERNAL,
    title: Annotated[str | None, Form()] = None,
    acl_groups: Annotated[str, Form()] = "",
) -> dict[str, Any]:
    _require_upload_role(scope)

    data = await file.read(MAX_UPLOAD_BYTES + 1)
    filename = file.filename or "document"
    try:
        validate_upload(data, filename)
    except UnparsableDocument as error:
        # A rejected upload is a user error, not a server error: say why, in their words.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(error)) from error

    groups = [key.strip() for key in acl_groups.split(",") if key.strip()]
    if visibility is Visibility.RESTRICTED and not groups:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="a restricted document needs at least one group in acl_groups",
        )
    unknown = [key for key in groups if key not in tenant.groups]
    if unknown:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"unknown groups: {unknown}")

    digest = content_hash(data)
    key = storage_key(str(tenant.tenant_id), digest, filename)
    from harbor_api.ingestion.service import storage_for

    storage_for(settings).put(key, data)

    document_id = uuid.uuid4()
    async with tenant_session(sessionmaker, scope.tenant_id) as session:
        source_id = await _upload_source_id(session, scope.tenant_id)
        # Re-uploading the same filename replaces that document rather than duplicating it.
        row = (
            await session.execute(
                text(
                    "INSERT INTO documents (id, tenant_id, source_id, external_id, title, "
                    "language, visibility, content_hash, status, storage_key, metadata) VALUES "
                    "(CAST(:id AS uuid), CAST(:tenant AS uuid), CAST(:source AS uuid), :external, "
                    ":title, :language, :visibility, :hash, :status, :key, "
                    "jsonb_build_object('filename', CAST(:filename AS text))) "
                    "ON CONFLICT (tenant_id, source_id, external_id) DO UPDATE SET "
                    "title = EXCLUDED.title, visibility = EXCLUDED.visibility, "
                    "content_hash = EXCLUDED.content_hash, status = EXCLUDED.status, "
                    "storage_key = EXCLUDED.storage_key, metadata = EXCLUDED.metadata, "
                    "version = documents.version + 1, updated_at = now() "
                    "RETURNING id"
                ),
                {
                    "id": str(document_id),
                    "tenant": str(scope.tenant_id),
                    "source": str(source_id),
                    "external": filename,
                    "title": title or filename,
                    "language": "auto",
                    "visibility": visibility.value,
                    "hash": digest,
                    "status": DocumentStatus.PENDING.value,
                    "key": key,
                    "filename": filename,
                },
            )
        ).one()
        document_id = uuid.UUID(str(row.id))

        await session.execute(
            text("DELETE FROM document_acl WHERE document_id = CAST(:d AS uuid)"),
            {"d": str(document_id)},
        )
        for key_name in groups:
            await session.execute(
                text(
                    "INSERT INTO document_acl (document_id, group_id) VALUES "
                    "(CAST(:d AS uuid), CAST(:g AS uuid))"
                ),
                {"d": str(document_id), "g": str(tenant.groups[key_name])},
            )

    await _dispatch(sessionmaker, scope.tenant_id, document_id, settings)
    return {"id": str(document_id), "status": DocumentStatus.PENDING.value, "filename": filename}


@router.post("/documents/{document_id}/reindex", status_code=status.HTTP_202_ACCEPTED)
async def reindex_document(
    document_id: uuid.UUID,
    scope: ScopeDep,
    settings: SettingsDep,
    sessionmaker: SessionmakerDep,
) -> dict[str, str]:
    """Re-parse a stored original — used after a parser or chunker change."""
    _require_upload_role(scope)
    async with tenant_session(sessionmaker, scope.tenant_id) as session:
        row = (
            await session.execute(
                text("SELECT storage_key FROM documents WHERE id = CAST(:id AS uuid)"),
                {"id": str(document_id)},
            )
        ).one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="document not found")
    if not row.storage_key:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="this document has no stored original (seeded fixture); re-run the seed",
        )

    await _dispatch(sessionmaker, scope.tenant_id, document_id, settings)
    return {"id": str(document_id), "status": DocumentStatus.PENDING.value}


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID,
    scope: ScopeDep,
    settings: SettingsDep,
    sessionmaker: SessionmakerDep,
) -> None:
    _require_upload_role(scope)
    async with tenant_session(sessionmaker, scope.tenant_id) as session:
        row = (
            await session.execute(
                text("DELETE FROM documents WHERE id = CAST(:id AS uuid) RETURNING storage_key"),
                {"id": str(document_id)},
            )
        ).one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="document not found")
    if row.storage_key:
        from harbor_api.ingestion.service import storage_for

        storage_for(settings).delete(row.storage_key)


async def _dispatch(
    sessionmaker: Any, tenant_id: uuid.UUID, document_id: uuid.UUID, settings: SettingsDep
) -> None:
    """Queue ingestion, or run it inline when no worker is available (tests, demo seed)."""
    if settings.ingest_eager:
        await ingest_document(
            sessionmaker, tenant_id=tenant_id, document_id=document_id, settings=settings
        )
        return
    ingest_document_task.delay(str(tenant_id), str(document_id))
