from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read from HARBOR_* environment variables."""

    model_config = SettingsConfigDict(env_prefix="HARBOR_", env_file=".env", extra="ignore")

    env: Literal["dev", "test", "staging", "prod"] = "dev"
    version: str = "0.1.0"
    log_level: str = "INFO"
    log_json: bool = True

    # The app connects as harbor_app, a non-superuser role, so Row-Level Security applies.
    # Migrations need the owner role instead (see migrations/env.py).
    database_url: str = "postgresql+asyncpg://harbor_app:harbor_app@localhost:5432/harbor"
    migration_database_url: str = "postgresql+psycopg://harbor:harbor@localhost:5432/harbor"
    redis_url: str = "redis://localhost:6379/0"
    readiness_timeout_s: float = Field(default=2.0, gt=0)

    cors_origins: list[str] = ["http://localhost:3000"]

    # --- Ingestion ---
    storage_root: Path = Path("./var/storage")
    # Run ingestion inline instead of dispatching to Celery: tests, and `make demo` seeding.
    ingest_eager: bool = False
    parser_profile: Literal["light", "docling"] = "light"

    # --- Demo (replaced by real auth in M2) ---
    demo_tenant_slug: str = "harbor-demo"
    demo_mode: bool = True

    # --- Models ---
    # Any OpenAI-compatible endpoint: mock-llm by default, then Azure OpenAI / OpenAI /
    # Qwen (DashScope) / DeepSeek.
    llm_base_url: str = "http://localhost:8100/v1"
    llm_api_key: str = "mock"
    llm_model_main: str = "mock-main"
    llm_model_small: str = "mock-small"
    llm_is_mock: bool = True
    # Ignore ambient proxy env vars when calling model endpoints (see llm/client.py).
    llm_trust_env: bool = False
    llm_timeout_connect_s: float = 5.0
    llm_timeout_first_token_s: float = 20.0
    llm_max_output_tokens: int = 700

    embedding_base_url: str = "http://localhost:8100/v1"
    embedding_api_key: str = "mock"
    embedding_model: str = "mock-embed"
    embedding_dimensions: int = 1536

    # --- Retrieval ---
    bm25_top_k: int = 50
    vector_top_k: int = 50
    rrf_k: int = 60
    context_top_k: int = 6
    context_token_budget: int = 2500
    # Above this many candidate chunks, switch from exact vector search to HNSW (ADR-0001).
    exact_search_max_chunks: int = 50_000
    # Heuristic answerability gate; calibrated against the eval set in M1b.
    # A passage joins the prompt only with real evidence: a BM25 score above this, or a
    # cosine similarity above min_vector_similarity. Deterministic mock embeddings score
    # near zero, so in demo mode this effectively keeps lexical hits only.
    # Calibrated on the demo corpus: genuine matches score 30+, incidental term overlap
    # lands near 3-5. BM25 scores are corpus-dependent, so M1b replaces this with a gate
    # calibrated on the eval set plus a reranker score.
    min_bm25_score: float = 8.0
    # Passages also have to score within this fraction of the best lexical hit. An absolute
    # floor alone either admits noise on strong queries or rejects everything on weak ones.
    bm25_relative_floor: float = 0.25
    min_vector_similarity: float = 0.25

    # --- Chat ---
    max_message_chars: int = 2000
    rate_limit_per_minute: int = 20


@lru_cache
def get_settings() -> Settings:
    return Settings()
