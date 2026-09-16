"""OpenAI-compatible LLM and embedding clients.

One wire format covers every provider Harbor targets: mock-llm in development, and
OpenAI, Azure OpenAI, Qwen (DashScope) and DeepSeek in deployments. Anthropic's native
API arrives in M2 behind the same interface.

Reliability rules that matter in production and are easy to get wrong:
  - Retry only *before the first token*. Once bytes have streamed to the user, retrying
    would duplicate a partial answer, so failures after that point propagate.
  - Fall back to the next provider in the chain under the same rule.
  - Always ask for usage, so cost accounting does not depend on token estimates.
"""

import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx
import structlog

log = structlog.get_logger(__name__)

RETRYABLE_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504})


class LLMError(RuntimeError):
    """No provider in the chain could serve the request."""


@dataclass(frozen=True)
class Provider:
    base_url: str
    api_key: str
    model: str
    name: str = "default"


@dataclass
class Delta:
    text: str


@dataclass
class Completed:
    model: str
    usage: dict[str, int] = field(default_factory=dict)
    finish_reason: str | None = None


StreamEvent = Delta | Completed


def _headers(provider: Provider) -> dict[str, str]:
    return {"authorization": f"Bearer {provider.api_key}", "content-type": "application/json"}


class ChatClient:
    def __init__(
        self,
        providers: Sequence[Provider],
        *,
        connect_timeout_s: float = 5.0,
        first_token_timeout_s: float = 20.0,
        max_attempts_per_provider: int = 2,
        trust_env: bool = False,
    ) -> None:
        if not providers:
            raise ValueError("at least one provider is required")
        self._providers = list(providers)
        self._timeout = httpx.Timeout(first_token_timeout_s, connect=connect_timeout_s)
        self._max_attempts = max_attempts_per_provider
        # Provider endpoints are configured explicitly, so ambient HTTP(S)_PROXY /
        # ALL_PROXY variables are ignored by default (a developer SOCKS proxy must not
        # silently sit between Harbor and a model API). Set trust_env when a deployment
        # genuinely requires an egress proxy.
        self._trust_env = trust_env

    async def stream(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int,
        temperature: float = 0.0,
        extra_headers: dict[str, str] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        last_error: Exception | None = None
        for provider in self._providers:
            for attempt in range(1, self._max_attempts + 1):
                started = False
                try:
                    async for event in self._stream_once(
                        provider, messages, max_tokens, temperature, extra_headers or {}
                    ):
                        started = True
                        yield event
                    return
                except Exception as error:  # normalised into LLMError below
                    if started:
                        # Bytes already reached the client; a retry would duplicate output.
                        raise
                    last_error = error
                    log.warning(
                        "llm.attempt_failed",
                        provider=provider.name,
                        attempt=attempt,
                        error=str(error),
                    )
        raise LLMError(f"all providers failed: {last_error}") from last_error

    async def _stream_once(
        self,
        provider: Provider,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
        extra_headers: dict[str, str],
    ) -> AsyncIterator[StreamEvent]:
        payload = {
            "model": provider.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        usage: dict[str, int] = {}
        finish_reason: str | None = None
        async with (
            httpx.AsyncClient(timeout=self._timeout, trust_env=self._trust_env) as client,
            client.stream(
                "POST",
                f"{provider.base_url}/chat/completions",
                json=payload,
                headers={**_headers(provider), **extra_headers},
            ) as response,
        ):
            if response.status_code in RETRYABLE_STATUS or response.status_code >= 400:
                await response.aread()
                raise LLMError(f"{provider.name} returned HTTP {response.status_code}")
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line.removeprefix("data: ").strip()
                if data == "[DONE]":
                    break
                chunk = json.loads(data)
                if chunk.get("usage"):
                    usage = chunk["usage"]
                for choice in chunk.get("choices") or []:
                    if content := (choice.get("delta") or {}).get("content"):
                        yield Delta(text=content)
                    if choice.get("finish_reason"):
                        finish_reason = choice["finish_reason"]
        yield Completed(model=provider.model, usage=usage, finish_reason=finish_reason)


class EmbeddingClient:
    def __init__(
        self,
        provider: Provider,
        *,
        dimensions: int,
        timeout_s: float = 30.0,
        trust_env: bool = False,
    ) -> None:
        self._provider = provider
        self._dimensions = dimensions
        self._timeout = timeout_s
        self._trust_env = trust_env  # see ChatClient

    @property
    def model(self) -> str:
        return self._provider.model

    async def embed(self, texts: Sequence[str], *, batch_size: int = 64) -> list[list[float]]:
        vectors: list[list[float]] = []
        async with httpx.AsyncClient(timeout=self._timeout, trust_env=self._trust_env) as client:
            for start in range(0, len(texts), batch_size):
                batch = list(texts[start : start + batch_size])
                payload: dict[str, Any] = {
                    "model": self._provider.model,
                    "input": batch,
                    "dimensions": self._dimensions,
                }
                response = await client.post(
                    f"{self._provider.base_url}/embeddings",
                    json=payload,
                    headers=_headers(self._provider),
                )
                if response.status_code >= 400:
                    raise LLMError(f"embeddings returned HTTP {response.status_code}")
                data = sorted(response.json()["data"], key=lambda item: item["index"])
                vectors.extend(item["embedding"] for item in data)
        return vectors
