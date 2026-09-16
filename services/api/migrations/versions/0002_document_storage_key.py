"""Track where each uploaded original is stored, and allow the 'parsing' status.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


STATUSES = ("pending", "parsing", "indexed", "failed")


def _replace_status_check(values: tuple[str, ...]) -> None:
    """Swap the status CHECK constraint.

    The constraint is dropped by definition rather than by name: SQLAlchemy generates the
    name for a non-native enum, and hardcoding it here would break the moment that changes.
    """
    op.execute(
        """
        DO $$
        DECLARE constraint_name text;
        BEGIN
            FOR constraint_name IN
                SELECT conname FROM pg_constraint
                WHERE conrelid = 'documents'::regclass AND contype = 'c'
                  AND pg_get_constraintdef(oid) LIKE '%indexed%'
            LOOP
                EXECUTE format('ALTER TABLE documents DROP CONSTRAINT %I', constraint_name);
            END LOOP;
        END
        $$;
        """
    )
    allowed = ", ".join(f"'{value}'" for value in values)
    op.execute(
        f"ALTER TABLE documents ADD CONSTRAINT document_status CHECK (status IN ({allowed}))"
    )


def upgrade() -> None:
    # Nullable: fixture documents live in the repository and have no stored original.
    op.add_column("documents", sa.Column("storage_key", sa.String(1024), nullable=True))
    _replace_status_check(STATUSES)


def downgrade() -> None:
    op.execute("UPDATE documents SET status = 'pending' WHERE status = 'parsing'")
    _replace_status_check(("pending", "indexed", "failed"))
    op.drop_column("documents", "storage_key")
