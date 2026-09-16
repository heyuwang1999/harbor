"""OpenAI-compatible fake LLM server.

Used by tests, E2E runs and k6 load tests so they measure Harbor, not a provider,
and cost nothing. Behaviour is controlled by env defaults, overridable per request
with headers:

  x-mock-status          force an HTTP error status (e.g. 429, 500)
  x-mock-error-rate      probability 0..1 of returning a 500
  x-mock-latency-ms      delay before the first byte
  x-mock-token-delay-ms  delay between streamed chunks
  x-mock-response        exact assistant text to return
"""

import asyncio
import hashlib
import json
import math
import os
import random
import re
import struct
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

MODELS = ["mock-small", "mock-main", "mock-judge", "mock-embed"]


class Behaviour(BaseModel):
    status: int | None = None
    error_rate: float = 0.0
    latency_ms: int = 0
    token_delay_ms: int = 0
    response: str | None = None

    @classmethod
    def from_request(cls, request: Request) -> "Behaviour":
        def pick(header: str, env: str) -> str | None:
            return request.headers.get(header) or os.environ.get(env)

        status = pick("x-mock-status", "MOCK_LLM_STATUS")
        return cls(
            status=int(status) if status else None,
            error_rate=float(pick("x-mock-error-rate", "MOCK_LLM_ERROR_RATE") or 0),
            latency_ms=int(pick("x-mock-latency-ms", "MOCK_LLM_LATENCY_MS") or 0),
            token_delay_ms=int(pick("x-mock-token-delay-ms", "MOCK_LLM_TOKEN_DELAY_MS") or 0),
            response=request.headers.get("x-mock-response"),
        )

    def forced_error(self) -> int | None:
        if self.status and self.status >= 400:
            return self.status
        if self.error_rate and random.random() < self.error_rate:
            return 500
        return None


def error_response(status: int) -> JSONResponse:
    kind = "rate_limit_exceeded" if status == 429 else "server_error"
    return JSONResponse(
        status_code=status,
        content={"error": {"message": f"mock {status}", "type": kind, "code": kind}},
    )


def count_tokens(text: str) -> int:
    # Rough heuristic, good enough for metering tests: ~4 chars/token, CJK chars ~1 token.
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return max(1, cjk + math.ceil((len(text) - cjk) / 4))


def message_text(message: dict[str, Any]) -> str:
    content = message.get("content") or ""
    if isinstance(content, list):  # multimodal content parts
        return " ".join(part.get("text", "") for part in content if isinstance(part, dict))
    return str(content)


SOURCE_BLOCK = re.compile(r'<source id="(S\d+)"[^>]*>\s*(.*?)\s*</source>', re.DOTALL)
SENTENCE_END = re.compile(r"(?<=[。！？!?])|(?<=\. )")
NO_SOURCES_REPLY = "I could not find an answer to that in the provided sources."


def grounded_reply(prompt: str, sentences_per_source: int = 2) -> str:
    """Answer from the <source> blocks in the prompt, with real citation markers.

    This keeps the offline demo honest: the text comes from the retrieved passages and
    the `[S#]` markers are real, so citation validation, the source drawer and the
    grounding checks all exercise the same code paths they would with a live model.
    It is extractive, not generative — no paraphrasing, no reasoning.
    """
    sources = SOURCE_BLOCK.findall(prompt)
    if not sources:
        return NO_SOURCES_REPLY

    parts = []
    for marker, body in sources[:2]:
        sentences = [s.strip() for s in SENTENCE_END.split(body.replace("\n", " ")) if s.strip()]
        excerpt = " ".join(sentences[:sentences_per_source]).strip()
        if excerpt:
            parts.append(f"{excerpt} [{marker}]")
    return " ".join(parts) if parts else NO_SOURCES_REPLY


def build_reply(body: dict[str, Any], behaviour: Behaviour) -> str:
    if behaviour.response is not None:
        return behaviour.response
    fmt = (body.get("response_format") or {}).get("type")
    if fmt in {"json_object", "json_schema"}:
        return "{}"
    messages = body.get("messages") or []
    last_user = next((message_text(m) for m in reversed(messages) if m.get("role") == "user"), "")
    if "<source id=" in last_user:
        return grounded_reply(last_user)
    return f"Mock answer to: {last_user[:200]}"


def embed(text: str, dimensions: int) -> list[float]:
    """Deterministic unit vector derived from the text hash."""
    values: list[float] = []
    counter = 0
    while len(values) < dimensions:
        digest = hashlib.sha256(f"{counter}:{text}".encode()).digest()
        values.extend(v / 2**31 - 1 for v in struct.unpack("<8I", digest))
        counter += 1
    vector = values[:dimensions]
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / norm for v in vector]


app = FastAPI(title="mock-llm")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/models")
async def models() -> dict[str, Any]:
    return {"object": "list", "data": [{"id": m, "object": "model"} for m in MODELS]}


@app.post("/v1/chat/completions", response_model=None)
async def chat_completions(request: Request) -> JSONResponse | StreamingResponse:
    body: dict[str, Any] = await request.json()
    behaviour = Behaviour.from_request(request)
    if (status := behaviour.forced_error()) is not None:
        return error_response(status)
    await asyncio.sleep(behaviour.latency_ms / 1000)

    model = body.get("model", "mock-main")
    reply = build_reply(body, behaviour)
    prompt_tokens = count_tokens(" ".join(message_text(m) for m in body.get("messages") or []))
    usage = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": count_tokens(reply),
        "total_tokens": prompt_tokens + count_tokens(reply),
    }
    completion_id = f"chatcmpl-mock-{uuid.uuid4().hex[:12]}"
    created = int(time.time())

    if not body.get("stream"):
        return JSONResponse(
            {
                "id": completion_id,
                "object": "chat.completion",
                "created": created,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": reply},
                        "finish_reason": "stop",
                    }
                ],
                "usage": usage,
            }
        )

    include_usage = bool((body.get("stream_options") or {}).get("include_usage"))

    async def events() -> AsyncIterator[str]:
        def chunk(delta: dict[str, Any], finish: str | None = None) -> str:
            payload = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }
            return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

        yield chunk({"role": "assistant", "content": ""})
        for piece in (reply[i : i + 4] for i in range(0, len(reply), 4)):
            await asyncio.sleep(behaviour.token_delay_ms / 1000)
            yield chunk({"content": piece})
        yield chunk({}, finish="stop")
        if include_usage:
            usage_payload = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [],
                "usage": usage,
            }
            yield f"data: {json.dumps(usage_payload)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


@app.post("/v1/embeddings", response_model=None)
async def embeddings(request: Request) -> JSONResponse:
    body: dict[str, Any] = await request.json()
    behaviour = Behaviour.from_request(request)
    if (status := behaviour.forced_error()) is not None:
        return error_response(status)
    await asyncio.sleep(behaviour.latency_ms / 1000)

    inputs = body.get("input", [])
    texts = [inputs] if isinstance(inputs, str) else list(inputs)
    dimensions = int(body.get("dimensions") or 1536)
    return JSONResponse(
        {
            "object": "list",
            "model": body.get("model", "mock-embed"),
            "data": [
                {"object": "embedding", "index": i, "embedding": embed(text, dimensions)}
                for i, text in enumerate(texts)
            ],
            "usage": {
                "prompt_tokens": sum(count_tokens(t) for t in texts),
                "total_tokens": sum(count_tokens(t) for t in texts),
            },
        }
    )
