import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from harbor_api.api.deps import ResourcesDep, ScopeDep, SessionmakerDep, SettingsDep
from harbor_api.api.sse import sse
from harbor_api.chat.service import ChatRequest, run_chat_turn
from harbor_api.core.ratelimit import RateLimitExceeded, check_rate_limit
from harbor_api.llm.registry import chat_client, embedding_client

router = APIRouter(prefix="/v1", tags=["chat"])


class ChatBody(BaseModel):
    message: str = Field(min_length=1)
    channel: str = "web"
    visitor_id: str = "anonymous"
    conversation_id: uuid.UUID | None = None


@router.post("/chat")
async def chat(
    body: ChatBody,
    scope: ScopeDep,
    settings: SettingsDep,
    sessionmaker: SessionmakerDep,
    resources: ResourcesDep,
) -> StreamingResponse:
    """Stream one grounded answer. Event contract: docs/specs/chat-stream.md."""
    if len(body.message) > settings.max_message_chars:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"message exceeds {settings.max_message_chars} characters",
        )
    try:
        await check_rate_limit(
            resources.redis,
            f"chat:{scope.tenant_id}:{body.visitor_id}",
            limit=settings.rate_limit_per_minute,
        )
    except RateLimitExceeded as exceeded:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many messages, please slow down",
            headers={"retry-after": str(exceeded.retry_after_s)},
        ) from exceeded

    async def stream() -> AsyncIterator[str]:
        async for event in run_chat_turn(
            sessionmaker=sessionmaker,
            scope=scope,
            request=ChatRequest(
                message=body.message,
                channel=body.channel,
                visitor_id=body.visitor_id,
                conversation_id=body.conversation_id,
            ),
            settings=settings,
            embedder=embedding_client(settings),
            chat_client=chat_client(settings),
        ):
            yield sse(event.name, event.data)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"cache-control": "no-cache", "x-accel-buffering": "no"},
    )
