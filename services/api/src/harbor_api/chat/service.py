"""One chat turn, start to finish.

Order matters and is deliberate:

    retrieve → gate → (refuse | generate) → validate → persist

The gate runs *before* the model, so an unanswerable question costs nothing and cannot be
answered from the model's own knowledge. Validation runs after generation, so an invented
citation or a link never reaches the user as-is. Both the answer and the evidence behind
it are persisted, which is what the trace panel, evals and the future admin console read.
"""

import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from harbor_api.core.config import Settings
from harbor_api.core.metrics import (
    CHAT_DURATION,
    CHAT_TTFT,
    CHAT_TURNS,
    CITATIONS_DROPPED,
    LINKS_STRIPPED,
    RETRIEVAL_STAGE,
)
from harbor_api.core.text import detect_script
from harbor_api.db.session import tenant_session
from harbor_api.generation.citations import ValidatedAnswer, validate_answer
from harbor_api.generation.prompt import build_messages, refusal_for
from harbor_api.llm.client import ChatClient, Completed, Delta, EmbeddingClient, LLMError
from harbor_api.models.chat import AnswerType
from harbor_api.retrieval.access import AccessScope
from harbor_api.retrieval.search import hybrid_search
from harbor_api.retrieval.types import RetrievalResult

log = structlog.get_logger(__name__)

HISTORY_TURNS = 4


@dataclass
class ChatEvent:
    name: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChatRequest:
    message: str
    channel: str = "web"
    visitor_id: str = "anonymous"
    conversation_id: uuid.UUID | None = None


async def _load_history(session: AsyncSession, conversation_id: uuid.UUID) -> list[dict[str, str]]:
    rows = (
        await session.execute(
            text(
                "SELECT role, content FROM messages WHERE conversation_id = CAST(:c AS uuid) "
                "ORDER BY created_at DESC LIMIT :limit"
            ),
            {"c": str(conversation_id), "limit": HISTORY_TURNS * 2},
        )
    ).all()
    return [{"role": row.role, "content": row.content} for row in reversed(rows)]


async def _ensure_conversation(
    session: AsyncSession, scope: AccessScope, request: ChatRequest, language: str
) -> uuid.UUID:
    if request.conversation_id is not None:
        return request.conversation_id
    conversation_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO conversations (id, tenant_id, channel, visitor_id, language) "
            "VALUES (CAST(:id AS uuid), CAST(:tenant_id AS uuid), :channel, :visitor_id, :language)"
        ),
        {
            "id": str(conversation_id),
            "tenant_id": str(scope.tenant_id),
            "channel": request.channel,
            "visitor_id": request.visitor_id,
            "language": language,
        },
    )
    return conversation_id


async def _insert_message(
    session: AsyncSession,
    *,
    scope: AccessScope,
    conversation_id: uuid.UUID,
    role: str,
    content: str,
    answer_type: AnswerType | None = None,
    answer: ValidatedAnswer | None = None,
    model: str | None = None,
    usage: dict[str, int] | None = None,
    latency_ms: int | None = None,
) -> uuid.UUID:
    message_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO messages (id, tenant_id, conversation_id, role, content, answer_type, "
            "confidence, citations, model, usage, latency_ms) VALUES (CAST(:id AS uuid), "
            "CAST(:tenant_id AS uuid), CAST(:conversation_id AS uuid), :role, :content, "
            ":answer_type, :confidence, CAST(:citations AS jsonb), :model, CAST(:usage AS jsonb), "
            ":latency_ms)"
        ),
        {
            "id": str(message_id),
            "tenant_id": str(scope.tenant_id),
            "conversation_id": str(conversation_id),
            "role": role,
            "content": content,
            "answer_type": answer_type.value if answer_type else None,
            "confidence": answer.confidence if answer else None,
            "citations": _json(answer.citations if answer else []),
            "model": model,
            "usage": _json(usage or {}),
            "latency_ms": latency_ms,
        },
    )
    return message_id


async def _insert_trace(
    session: AsyncSession,
    *,
    scope: AccessScope,
    message_id: uuid.UUID,
    result: RetrievalResult,
) -> uuid.UUID:
    trace_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO retrieval_traces (id, tenant_id, message_id, query, normalized_query, "
            "strategy, candidates, timings_ms) VALUES (CAST(:id AS uuid), "
            "CAST(:tenant_id AS uuid), CAST(:message_id AS uuid), :query, :normalized_query, "
            "CAST(:strategy AS jsonb), "
            "CAST(:candidates AS jsonb), CAST(:timings AS jsonb))"
        ),
        {
            "id": str(trace_id),
            "tenant_id": str(scope.tenant_id),
            "message_id": str(message_id),
            "query": result.query,
            "normalized_query": result.normalized_query,
            "strategy": _json(result.strategy),
            "candidates": _json([candidate.as_trace() for candidate in result.candidates]),
            "timings": _json(result.timings_ms),
        },
    )
    return trace_id


def _json(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, default=str)


async def run_chat_turn(
    *,
    sessionmaker: async_sessionmaker[AsyncSession],
    scope: AccessScope,
    request: ChatRequest,
    settings: Settings,
    embedder: EmbeddingClient,
    chat_client: ChatClient,
) -> AsyncIterator[ChatEvent]:
    started = time.perf_counter()
    language = detect_script(request.message)

    async with tenant_session(sessionmaker, scope.tenant_id) as session:
        conversation_id = await _ensure_conversation(session, scope, request, language)
        history = await _load_history(session, conversation_id)
        await _insert_message(
            session,
            scope=scope,
            conversation_id=conversation_id,
            role="user",
            content=request.message,
        )

        yield ChatEvent(
            "meta",
            {
                "conversation_id": str(conversation_id),
                "language": language,
                "role": scope.role,
                "model": settings.llm_model_main,
                "mock": settings.llm_is_mock,
            },
        )
        yield ChatEvent("status", {"stage": "retrieving"})

        result = await hybrid_search(
            session, scope=scope, query=request.message, settings=settings, embedder=embedder
        )
        for stage, milliseconds in result.timings_ms.items():
            RETRIEVAL_STAGE.labels(stage=stage).observe(milliseconds / 1000)

        yield ChatEvent(
            "status",
            {
                "stage": "generating",
                "retrieved": len(result.context),
                "candidates": len(result.candidates),
            },
        )

        answer_type = AnswerType.ANSWERED
        model_used: str | None = None
        usage: dict[str, int] = {}
        raw_answer = ""

        if not result.answerable:
            answer_type = AnswerType.REFUSED
            raw_answer = refusal_for(request.message)
            yield ChatEvent("delta", {"text": raw_answer})
        else:
            messages = build_messages(request.message, result.context, history)
            first_token_at: float | None = None
            try:
                async for event in chat_client.stream(
                    messages, max_tokens=settings.llm_max_output_tokens
                ):
                    if isinstance(event, Delta):
                        if first_token_at is None:
                            first_token_at = time.perf_counter()
                            CHAT_TTFT.observe(first_token_at - started)
                        raw_answer += event.text
                        yield ChatEvent("delta", {"text": event.text})
                    elif isinstance(event, Completed):
                        model_used, usage = event.model, event.usage
            except LLMError as error:
                log.error("chat.generation_failed", error=str(error))
                answer_type = AnswerType.ERROR
                yield ChatEvent("error", {"message": "The model is unavailable. Please retry."})

        validated = validate_answer(raw_answer, result.context)
        if answer_type is AnswerType.ANSWERED and not validated.is_grounded:
            # The model answered without citing anything it was given: treat as ungrounded
            # rather than showing an answer the user cannot verify.
            answer_type = AnswerType.REFUSED
            validated.text = refusal_for(request.message)
        CITATIONS_DROPPED.inc(validated.dropped_citations)
        LINKS_STRIPPED.inc(validated.stripped_links)

        latency_ms = int((time.perf_counter() - started) * 1000)
        message_id = await _insert_message(
            session,
            scope=scope,
            conversation_id=conversation_id,
            role="assistant",
            content=validated.text,
            answer_type=answer_type,
            answer=validated,
            model=model_used,
            usage=usage,
            latency_ms=latency_ms,
        )
        trace_id = await _insert_trace(session, scope=scope, message_id=message_id, result=result)

    CHAT_TURNS.labels(answer_type=answer_type.value, channel=request.channel).inc()
    CHAT_DURATION.observe(time.perf_counter() - started)

    yield ChatEvent("citations", {"citations": validated.citations})
    yield ChatEvent(
        "done",
        {
            "message_id": str(message_id),
            "conversation_id": str(conversation_id),
            "trace_id": str(trace_id),
            # The validated text is authoritative: deltas are raw model output, this is
            # what survives citation checks and link stripping.
            "text": validated.text,
            "answer_type": answer_type.value,
            "confidence": validated.confidence,
            "dropped_citations": validated.dropped_citations,
            "stripped_links": validated.stripped_links,
            "model": model_used,
            "usage": usage,
            "latency_ms": latency_ms,
            "timings_ms": result.timings_ms,
            "strategy": result.strategy,
            "trace": [candidate.as_trace() for candidate in result.candidates[:12]],
        },
    )
