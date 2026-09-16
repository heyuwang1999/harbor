"""Document listing and passage lookup, both filtered by the caller's access scope."""

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import text

from harbor_api.api.deps import ScopeDep, SessionmakerDep, TenantDep
from harbor_api.db.session import tenant_session

router = APIRouter(prefix="/v1", tags=["documents"])


@router.get("/documents")
async def list_documents(
    scope: ScopeDep, tenant: TenantDep, sessionmaker: SessionmakerDep
) -> dict[str, Any]:
    async with tenant_session(sessionmaker, scope.tenant_id) as session:
        rows = (
            await session.execute(
                text(
                    "SELECT d.id, d.title, d.language, d.visibility, d.updated_at, "
                    "count(c.id) AS chunks FROM documents d "
                    "LEFT JOIN chunks c ON c.document_id = d.id "
                    "WHERE d.tenant_id = CAST(:tenant_id AS uuid) "
                    "AND d.visibility = ANY(CAST(:visibilities AS text[])) "
                    "GROUP BY d.id ORDER BY d.title"
                ),
                scope.params,
            )
        ).all()
    return {
        "tenant": {"slug": tenant.slug, "name": tenant.name},
        "role": scope.role,
        "documents": [
            {
                "id": str(row.id),
                "title": row.title,
                "language": row.language,
                "visibility": row.visibility,
                "chunks": row.chunks,
                "updated_at": row.updated_at,
            }
            for row in rows
        ],
    }


@router.get("/chunks/{chunk_id}")
async def get_chunk(
    chunk_id: uuid.UUID, scope: ScopeDep, sessionmaker: SessionmakerDep
) -> dict[str, Any]:
    """Fetch one passage for the source drawer — access-checked, not trusted from the UI."""
    async with tenant_session(sessionmaker, scope.tenant_id) as session:
        row = (
            await session.execute(
                text(
                    "SELECT c.id, c.text, c.heading_path, c.ordinal, c.visibility, "  # noqa: S608
                    "d.title AS document_title, d.language FROM chunks c "
                    "JOIN documents d ON d.id = c.document_id "
                    f"WHERE c.id = CAST(:chunk_id AS uuid) AND {scope.sql_filter}",
                ),
                {**scope.params, "chunk_id": str(chunk_id)},
            )
        ).one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="passage not found")
    return {
        "id": str(row.id),
        "text": row.text,
        "heading_path": list(row.heading_path or []),
        "ordinal": row.ordinal,
        "visibility": row.visibility,
        "document_title": row.document_title,
        "language": row.language,
    }
