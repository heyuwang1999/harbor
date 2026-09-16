"""Seed the demo tenant from the markdown fixtures.

Idempotent: a document whose content hash is unchanged is skipped, and a changed one has
its chunks replaced inside a single transaction, so the index is never half-updated. The
real ingestion pipeline (M1b) follows the same contract with Docling in front of it.

Runs as the database owner: it creates the tenant row itself, which the tenant-scoped RLS
policies would otherwise hide. Request paths use the harbor_app role instead.
"""

import asyncio
import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog
import yaml
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from harbor_api.core.config import Settings, get_settings
from harbor_api.core.logging import configure_logging
from harbor_api.core.text import normalize
from harbor_api.ingestion.chunking import CHUNKER_VERSION, chunk_markdown
from harbor_api.llm.registry import embedding_client
from harbor_api.models import (
    Chunk,
    Document,
    DocumentACL,
    Group,
    Source,
    Tenant,
    Visibility,
)
from harbor_api.models.content import DocumentStatus, SourceKind

log = structlog.get_logger(__name__)

FIXTURES = Path(__file__).parent / "fixtures"
TENANT_NAME = "Harbor Demo Co. (海港餅店)"
GROUPS = {"everyone": "Everyone", "staff": "Staff", "management": "Management"}

# Which principals a passage is exposed to. A caller presents the groups they hold
# (visitor = everyone; staff = everyone+staff; management = everyone+staff+management).
VISIBILITY_PRINCIPALS = {
    Visibility.PUBLIC: ["everyone"],
    Visibility.INTERNAL: ["staff"],
}


@dataclass
class SeedReport:
    documents_indexed: int = 0
    documents_skipped: int = 0
    chunks: int = 0

    def __str__(self) -> str:
        return (
            f"indexed {self.documents_indexed} document(s), "
            f"skipped {self.documents_skipped} unchanged, {self.chunks} chunk(s) written"
        )


@dataclass(frozen=True)
class Fixture:
    external_id: str
    title: str
    language: str
    visibility: Visibility
    acl: list[str]
    body: str
    content_hash: str

    @classmethod
    def load(cls, path: Path) -> "Fixture":
        raw = path.read_text(encoding="utf-8")
        if not raw.startswith("---"):
            raise ValueError(f"{path.name}: missing front matter")
        _, front_matter, body = raw.split("---", 2)
        meta: dict[str, Any] = yaml.safe_load(front_matter) or {}
        return cls(
            external_id=meta["external_id"],
            title=meta["title"],
            language=meta["language"],
            visibility=Visibility(meta["visibility"]),
            acl=list(meta.get("acl") or []),
            body=body.strip(),
            # Chunker version is part of the hash: changing chunking re-indexes.
            content_hash=hashlib.sha256(f"{CHUNKER_VERSION}:{raw}".encode()).hexdigest(),
        )


def owner_async_url(settings: Settings) -> str:
    """Owner credentials from the migration URL, on the async driver."""
    return settings.migration_database_url.replace("+psycopg", "+asyncpg")


async def _upsert_tenant(session: AsyncSession, slug: str) -> Tenant:
    tenant = (await session.execute(select(Tenant).where(Tenant.slug == slug))).scalar_one_or_none()
    if tenant is None:
        tenant = Tenant(slug=slug, name=TENANT_NAME, settings={"demo": True})
        session.add(tenant)
        await session.flush()
    return tenant


async def _upsert_groups(session: AsyncSession, tenant: Tenant) -> dict[str, uuid.UUID]:
    existing = {
        group.key: group
        for group in (
            await session.execute(select(Group).where(Group.tenant_id == tenant.id))
        ).scalars()
    }
    for key, name in GROUPS.items():
        if key not in existing:
            group = Group(tenant_id=tenant.id, key=key, name=name)
            session.add(group)
            existing[key] = group
    await session.flush()
    return {key: group.id for key, group in existing.items()}


async def _upsert_source(session: AsyncSession, tenant: Tenant) -> Source:
    source = (
        await session.execute(
            select(Source).where(Source.tenant_id == tenant.id, Source.name == "Demo fixtures")
        )
    ).scalar_one_or_none()
    if source is None:
        source = Source(
            tenant_id=tenant.id, kind=SourceKind.FIXTURE, name="Demo fixtures", config={}
        )
        session.add(source)
        await session.flush()
    return source


async def seed(settings: Settings | None = None) -> SeedReport:
    settings = settings or get_settings()
    engine = create_async_engine(owner_async_url(settings))
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    embedder = embedding_client(settings)
    report = SeedReport()

    try:
        async with sessionmaker() as session, session.begin():
            tenant = await _upsert_tenant(session, settings.demo_tenant_slug)
            groups = await _upsert_groups(session, tenant)
            source = await _upsert_source(session, tenant)

            for path in sorted(FIXTURES.glob("*.md")):
                fixture = Fixture.load(path)
                document = (
                    await session.execute(
                        select(Document).where(
                            Document.tenant_id == tenant.id,
                            Document.source_id == source.id,
                            Document.external_id == fixture.external_id,
                        )
                    )
                ).scalar_one_or_none()

                if document and document.content_hash == fixture.content_hash:
                    report.documents_skipped += 1
                    continue

                if document is None:
                    document = Document(
                        tenant_id=tenant.id,
                        source_id=source.id,
                        external_id=fixture.external_id,
                        title=fixture.title,
                        language=fixture.language,
                        visibility=fixture.visibility,
                        content_hash=fixture.content_hash,
                        status=DocumentStatus.PENDING,
                        doc_metadata={"fixture": path.name},
                    )
                    session.add(document)
                    await session.flush()
                else:
                    document.title = fixture.title
                    document.visibility = fixture.visibility
                    document.content_hash = fixture.content_hash
                    document.version += 1
                    await session.execute(delete(Chunk).where(Chunk.document_id == document.id))

                await session.execute(
                    delete(DocumentACL).where(DocumentACL.document_id == document.id)
                )
                principal_keys = VISIBILITY_PRINCIPALS.get(fixture.visibility, fixture.acl)
                for key in principal_keys:
                    if fixture.visibility is Visibility.RESTRICTED:
                        session.add(DocumentACL(document_id=document.id, group_id=groups[key]))
                principals = [groups[key] for key in principal_keys]

                pieces = chunk_markdown(fixture.body, title=fixture.title)
                vectors = await embedder.embed([piece.embed_text for piece in pieces])
                for piece, vector in zip(pieces, vectors, strict=True):
                    session.add(
                        Chunk(
                            tenant_id=tenant.id,
                            document_id=document.id,
                            ordinal=piece.ordinal,
                            heading_path=piece.heading_path,
                            text=piece.text,
                            text_norm=piece.index_text,
                            token_count=piece.token_count,
                            embedding=vector,
                            embedding_model=embedder.model,
                            visibility=fixture.visibility,
                            allowed_principals=principals,
                        )
                    )
                document.status = DocumentStatus.INDEXED
                report.documents_indexed += 1
                report.chunks += len(pieces)
                log.info(
                    "demo.document_indexed",
                    document=fixture.external_id,
                    chunks=len(pieces),
                    visibility=fixture.visibility.value,
                )
    finally:
        await engine.dispose()
    return report


def normalize_query(text: str) -> str:
    """Exposed for the seed CLI's self-check and for scripts."""
    return normalize(text)


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    report = asyncio.run(seed(settings))
    print(f"Harbor demo seed: {report}")


if __name__ == "__main__":
    main()
