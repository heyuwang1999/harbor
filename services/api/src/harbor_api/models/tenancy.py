import uuid
from typing import Any

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from harbor_api.db.base import Base, Timestamps, UUIDPrimaryKey


class Tenant(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "tenants"

    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class Group(UUIDPrimaryKey, Timestamps, Base):
    """A principal used for document access control (`everyone`, `staff`, `management`).

    M2 maps these to Better Auth organisation teams; the retrieval filter does not care
    where membership comes from, only which group ids the caller holds.
    """

    __tablename__ = "groups"
    __table_args__ = (UniqueConstraint("tenant_id", "key"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200))
