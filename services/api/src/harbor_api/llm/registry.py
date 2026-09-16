"""Builds clients from settings.

Tiers exist so cost and latency can be traded per call site: `small` handles cheap
classification-style work, `main` writes answers. In M2 these become per-tenant model
profiles (`hk` = Azure OpenAI + Qwen, `global` = Anthropic/OpenAI), and the fallback
chain gains a second provider.
"""

from harbor_api.core.config import Settings
from harbor_api.llm.client import ChatClient, EmbeddingClient, Provider


def chat_client(settings: Settings, *, tier: str = "main") -> ChatClient:
    model = settings.llm_model_main if tier == "main" else settings.llm_model_small
    provider = Provider(
        base_url=settings.llm_base_url.rstrip("/"),
        api_key=settings.llm_api_key,
        model=model,
        name="mock" if settings.llm_is_mock else "primary",
    )
    return ChatClient(
        [provider],
        connect_timeout_s=settings.llm_timeout_connect_s,
        first_token_timeout_s=settings.llm_timeout_first_token_s,
        trust_env=settings.llm_trust_env,
    )


def embedding_client(settings: Settings) -> EmbeddingClient:
    provider = Provider(
        base_url=settings.embedding_base_url.rstrip("/"),
        api_key=settings.embedding_api_key,
        model=settings.embedding_model,
    )
    return EmbeddingClient(
        provider, dimensions=settings.embedding_dimensions, trust_env=settings.llm_trust_env
    )
