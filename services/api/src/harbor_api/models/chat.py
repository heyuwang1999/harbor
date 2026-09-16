import enum
import uuid
from typing import Any

from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from harbor_api.db.base import Base, Timestamps, UUIDPrimaryKey
from harbor_api.models.content import _enum


class Channel(enum.StrEnum):
    WEB = "web"
    WIDGET = "widget"
    WHATSAPP = "whatsapp"


class AnswerType(enum.StrEnum):
    ANSWERED = "answered"
    REFUSED = "refused"  # nothing relevant retrieved, or the model found no support
    ERROR = "error"


class Conversation(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "conversations"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    channel: Mapped[Channel] = mapped_column(_enum(Channel, "channel"))
    visitor_id: Mapped[str] = mapped_column(String(128))
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)


class Message(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "messages"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    role: Mapped[str] = mapped_column(String(16))  # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text)
    answer_type: Mapped[AnswerType | None] = mapped_column(
        _enum(AnswerType, "answer_type"), nullable=True
    )
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    citations: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    usage: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)


class RetrievalTrace(UUIDPrimaryKey, Timestamps, Base):
    """Why these passages: per-stage ranks and timings, shown in the demo trace panel
    and used to debug answers. The M2 admin console reads the same rows."""

    __tablename__ = "retrieval_traces"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"))
    query: Mapped[str] = mapped_column(Text)
    normalized_query: Mapped[str] = mapped_column(Text)
    strategy: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    candidates: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    timings_ms: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class Feedback(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "feedback"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"))
    rating: Mapped[int] = mapped_column(Integer)  # +1 / -1
    reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
