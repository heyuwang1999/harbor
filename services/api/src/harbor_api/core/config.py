from functools import lru_cache
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

    database_url: str = "postgresql+asyncpg://harbor:harbor@localhost:5432/harbor"
    redis_url: str = "redis://localhost:6379/0"
    readiness_timeout_s: float = Field(default=2.0, gt=0)

    cors_origins: list[str] = ["http://localhost:3000"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
