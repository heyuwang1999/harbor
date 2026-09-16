"""Integration tests run against a real ParadeDB, because the behaviour under test is SQL:
BM25 with the Jieba tokenizer, filtered vector search, and Row-Level Security.

Locally that is the compose stack (`make infra`); in CI it is a service container. They are
skipped when no database is reachable, so `make check` still works offline.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator, Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from harbor_api.core.config import Settings
from harbor_api.demo.seed import owner_async_url, seed
from harbor_api.models import Group, Tenant
from harbor_api.retrieval.access import scope_for_role


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(env="test", log_json=False)


@pytest.fixture(scope="session")
def database_available(settings: Settings) -> bool:
    async def probe() -> bool:
        engine = create_async_engine(owner_async_url(settings))
        try:
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            return True
        except Exception:
            return False
        finally:
            await engine.dispose()

    return asyncio.run(probe())


@pytest.fixture(scope="session")
def seeded(settings: Settings, database_available: bool) -> None:
    if not database_available:
        pytest.skip("no database reachable; run `make infra` (see tests/integration/conftest.py)")
    asyncio.run(seed(settings))


@pytest.fixture
async def owner_session(settings: Settings, seeded: None) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(owner_async_url(settings))
    try:
        async with async_sessionmaker(engine)() as session:
            yield session
    finally:
        await engine.dispose()


@pytest.fixture
async def demo_tenant(owner_session: AsyncSession, settings: Settings) -> Tenant:
    result = await owner_session.execute(
        text("SELECT id, slug, name FROM tenants WHERE slug = :slug"),
        {"slug": settings.demo_tenant_slug},
    )
    row = result.one()
    tenant = Tenant(id=row.id, slug=row.slug, name=row.name)
    return tenant


@pytest.fixture
async def demo_groups(owner_session: AsyncSession, demo_tenant: Tenant) -> dict[str, uuid.UUID]:
    rows = (
        await owner_session.execute(
            text("SELECT key, id FROM groups WHERE tenant_id = :tenant_id"),
            {"tenant_id": demo_tenant.id},
        )
    ).all()
    return {row.key: row.id for row in rows}


@pytest.fixture
def scope_factory(demo_tenant: Tenant, demo_groups: dict[str, uuid.UUID]) -> Iterator[object]:
    def make(role: str):  # type: ignore[no-untyped-def]
        return scope_for_role(demo_tenant.id, role, demo_groups)

    yield make


__all__ = ["Group"]
