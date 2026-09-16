import enum
import uuid
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import Enum, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from harbor_api.db.base import Base, Timestamps, UUIDPrimaryKey

# Fixed at the OpenAI text-embedding-3-small size; changing it needs a migration plus a
# re-embed job (planned for M1b, when the embedding model is chosen by eval).
EMBEDDING_DIM = 1536


class Visibility(enum.StrEnum):
    PUBLIC = "public"  # customer channels: widget, WhatsApp
    INTERNAL = "internal"  # signed-in staff
    RESTRICTED = "restricted"  # only groups listed in document_acl


class SourceKind(enum.StrEnum):
    FIXTURE = "fixture"
    UPLOAD = "upload"
    URL = "url"


class DocumentStatus(enum.StrEnum):
    PENDING = "pending"
    INDEXED = "indexed"
    FAILED = "failed"


def _enum(enum_type: type[enum.StrEnum], name: str) -> Enum:
    # VARCHAR + CHECK instead of a native PG enum: adding a value later is a plain migration.
    return Enum(
        enum_type,
        name=name,
        native_enum=False,
        values_callable=lambda e: [m.value for m in e],
    )


class Source(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "sources"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    kind: Mapped[SourceKind] = mapped_column(_enum(SourceKind, "source_kind"))
    name: Mapped[str] = mapped_column(String(200))
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class Document(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("tenant_id", "source_id", "external_id"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"))
    external_id: Mapped[str] = mapped_column(String(512))
    title: Mapped[str] = mapped_column(String(512))
    language: Mapped[str] = mapped_column(String(16))
    visibility: Mapped[Visibility] = mapped_column(_enum(Visibility, "visibility"))
    content_hash: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[DocumentStatus] = mapped_column(_enum(DocumentStatus, "document_status"))
    doc_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)


class DocumentACL(Base):
    """Which groups may see a restricted document."""

    __tablename__ = "document_acl"

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    group_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True
    )


class Chunk(UUIDPrimaryKey, Timestamps, Base):
    """A retrievable passage.

    `visibility` and `allowed_principals` are denormalised from the document so that one
    filtered query can enforce access without a join (see retrieval/filters.py). The seed
    and, later, the ingestion pipeline keep them in sync inside the same transaction.
    """

    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "ordinal"),
        Index("ix_chunks_tenant_document", "tenant_id", "document_id", "ordinal"),
        Index("ix_chunks_allowed_principals", "allowed_principals", postgresql_using="gin"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    ordinal: Mapped[int] = mapped_column(Integer)
    heading_path: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    text: Mapped[str] = mapped_column(Text)
    text_norm: Mapped[str] = mapped_column(Text)  # OpenCC hk2s, indexed by BM25 (ADR-0001)
    token_count: Mapped[int] = mapped_column(Integer)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    visibility: Mapped[Visibility] = mapped_column(_enum(Visibility, "visibility"))
    allowed_principals: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)))
