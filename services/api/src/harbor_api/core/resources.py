from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from harbor_api.core.config import Settings

HealthCheck = Callable[[], Awaitable[None]]


@dataclass
class Resources:
    """Process-wide connections, created in the app lifespan."""

    engine: AsyncEngine
    sessionmaker: async_sessionmaker[AsyncSession]
    redis: Redis

    @classmethod
    def create(cls, settings: Settings) -> "Resources":
        engine = create_async_engine(settings.database_url, pool_pre_ping=True)
        return cls(
            engine=engine,
            sessionmaker=async_sessionmaker(engine, expire_on_commit=False),
            redis=Redis.from_url(settings.redis_url),
        )

    def readiness_checks(self) -> dict[str, HealthCheck]:
        async def database() -> None:
            async with self.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))

        async def redis() -> None:
            await self.redis.ping()

        return {"database": database, "redis": redis}

    async def close(self) -> None:
        await self.redis.aclose()
        await self.engine.dispose()
