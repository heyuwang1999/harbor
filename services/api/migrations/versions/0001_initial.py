"""Initial schema: tenancy, documents, chunks, chat, plus RLS and search indexes.

Revision ID: 0001
Revises:
Create Date: 2026-09-16
"""

import os
from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBEDDING_DIM = 1536
APP_ROLE = "harbor_app"

# Tables carrying tenant_id get the same tenant-isolation policy.
TENANT_TABLES = (
    "groups",
    "sources",
    "documents",
    "chunks",
    "conversations",
    "messages",
    "retrieval_traces",
    "feedback",
)

VISIBILITY = sa.Enum(
    "public", "internal", "restricted", name="visibility", native_enum=False, create_constraint=True
)


def _enum(*values: str, name: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_search")

    timestamps = (
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )

    op.create_table(
        "tenants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("slug", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("settings", postgresql.JSONB, nullable=False, server_default="{}"),
        *timestamps,
    )

    op.create_table(
        "groups",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.UniqueConstraint("tenant_id", "key"),
        *timestamps,
    )

    op.create_table(
        "sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", _enum("fixture", "upload", "url", name="source_kind"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("config", postgresql.JSONB, nullable=False, server_default="{}"),
        *timestamps,
    )

    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("external_id", sa.String(512), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("language", sa.String(16), nullable=False),
        sa.Column("visibility", VISIBILITY, nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column(
            "status", _enum("pending", "indexed", "failed", name="document_status"), nullable=False
        ),
        sa.Column("metadata", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.UniqueConstraint("tenant_id", "source_id", "external_id"),
        *timestamps,
    )

    op.create_table(
        "document_acl",
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "group_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("groups.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )

    op.create_table(
        "chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer, nullable=False),
        sa.Column("heading_path", postgresql.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("text_norm", sa.Text, nullable=False),
        sa.Column("token_count", sa.Integer, nullable=False),
        sa.Column("embedding", pgvector.sqlalchemy.Vector(EMBEDDING_DIM), nullable=True),
        sa.Column("embedding_model", sa.String(128), nullable=True),
        sa.Column("visibility", VISIBILITY, nullable=False),
        sa.Column(
            "allowed_principals", postgresql.ARRAY(postgresql.UUID(as_uuid=True)), nullable=False
        ),
        sa.UniqueConstraint("document_id", "ordinal"),
        *timestamps,
    )
    op.create_index("ix_chunks_tenant_document", "chunks", ["tenant_id", "document_id", "ordinal"])
    op.create_index(
        "ix_chunks_allowed_principals", "chunks", ["allowed_principals"], postgresql_using="gin"
    )
    # BM25 over the OpenCC-normalised text with the Jieba tokenizer (ADR-0001). tenant_id and
    # visibility are indexed too so pg_search pushes those filters into the Tantivy query.
    op.execute(
        "CREATE INDEX ix_chunks_bm25 ON chunks USING bm25 "
        "(id, tenant_id, visibility, (text_norm::pdb.jieba)) WITH (key_field='id')"
    )
    # Only used above exact_search_max_chunks; below it Harbor filters first and sorts exactly.
    op.execute(
        "CREATE INDEX ix_chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops)"
    )

    op.create_table(
        "conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("channel", _enum("web", "widget", "whatsapp", name="channel"), nullable=False),
        sa.Column("visitor_id", sa.String(128), nullable=False),
        sa.Column("language", sa.String(16), nullable=True),
        *timestamps,
    )

    op.create_table(
        "messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column(
            "answer_type", _enum("answered", "refused", "error", name="answer_type"), nullable=True
        ),
        sa.Column("confidence", sa.Float, nullable=True),
        sa.Column("citations", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("usage", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("latency_ms", sa.Integer, nullable=True),
        *timestamps,
    )
    op.create_index("ix_messages_conversation", "messages", ["conversation_id", "created_at"])

    op.create_table(
        "retrieval_traces",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "message_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("query", sa.Text, nullable=False),
        sa.Column("normalized_query", sa.Text, nullable=False),
        sa.Column("strategy", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("candidates", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("timings_ms", postgresql.JSONB, nullable=False, server_default="{}"),
        *timestamps,
    )

    op.create_table(
        "feedback",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "message_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("rating", sa.Integer, nullable=False),
        sa.Column("reason", sa.String(64), nullable=True),
        sa.Column("comment", sa.Text, nullable=True),
        *timestamps,
    )

    _create_app_role()
    _enable_rls()


def _create_app_role() -> None:
    """Create the least-privilege role the application connects as.

    Superusers and table owners bypass RLS, so the app must not use the owner role.
    """
    password = os.environ.get("HARBOR_APP_DB_PASSWORD", APP_ROLE)
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{password}';
            END IF;
        END
        $$;
        """
    )
    op.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}")
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}")
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP_ROLE}"
    )


def _enable_rls() -> None:
    # The policy expression must never evaluate to NULL. pg_search pushes predicates into
    # its index scan and rejects a NULL one ("pushdown expression should not evaluate to
    # NULL"), which would turn a missing tenant context into an error instead of an empty
    # result. COALESCE to the nil UUID keeps it fail-closed and boring: no context, no rows.
    tenant = (
        "COALESCE(NULLIF(current_setting('harbor.tenant_id', true), '')::uuid, "
        "'00000000-0000-0000-0000-000000000000'::uuid)"
    )
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING (tenant_id = {tenant}) WITH CHECK (tenant_id = {tenant})"
        )
    # document_acl has no tenant_id of its own; scope it through its document.
    op.execute("ALTER TABLE document_acl ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON document_acl USING (EXISTS ("
        f"SELECT 1 FROM documents d WHERE d.id = document_id AND d.tenant_id = {tenant})) "
        "WITH CHECK (EXISTS ("
        f"SELECT 1 FROM documents d WHERE d.id = document_id AND d.tenant_id = {tenant}))"
    )


def downgrade() -> None:
    for table in ("document_acl", *TENANT_TABLES):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
    for table in (
        "feedback",
        "retrieval_traces",
        "messages",
        "conversations",
        "chunks",
        "document_acl",
        "documents",
        "sources",
        "groups",
        "tenants",
    ):
        op.drop_table(table)
