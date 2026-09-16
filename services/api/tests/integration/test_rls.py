"""Row-Level Security is the backstop for tenant isolation.

These tests connect as `harbor_app`, the role the application actually uses. Running them
as the owner would prove nothing: table owners and superusers bypass RLS.
"""

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from harbor_api.core.config import Settings
from harbor_api.db.session import tenant_session
from harbor_api.models import Tenant

OTHER_SLUG = "rls-probe-tenant"


@pytest.fixture
async def other_tenant(owner_session: AsyncSession) -> AsyncIterator[uuid.UUID]:
    """A second tenant with one chunk, created and removed via the owner connection."""
    tenant_id = uuid.uuid4()
    await owner_session.execute(
        text("INSERT INTO tenants (id, slug, name) VALUES (:id, :slug, :name)"),
        {"id": tenant_id, "slug": OTHER_SLUG, "name": "Other Tenant"},
    )
    source_id, document_id = uuid.uuid4(), uuid.uuid4()
    await owner_session.execute(
        text(
            "INSERT INTO sources (id, tenant_id, kind, name) "
            "VALUES (:id, :tenant_id, 'fixture', 'probe')"
        ),
        {"id": source_id, "tenant_id": tenant_id},
    )
    await owner_session.execute(
        text(
            "INSERT INTO documents (id, tenant_id, source_id, external_id, title, language, "
            "visibility, content_hash, status) VALUES (:id, :tenant_id, :source_id, 'probe', "
            "'Other tenant secret', 'en', 'public', 'hash', 'indexed')"
        ),
        {"id": document_id, "tenant_id": tenant_id, "source_id": source_id},
    )
    await owner_session.execute(
        text(
            "INSERT INTO chunks (id, tenant_id, document_id, ordinal, text, text_norm, "
            "token_count, visibility, allowed_principals) VALUES (:id, :tenant_id, :document_id, "
            "0, 'other tenant secret passage', 'other tenant secret passage', 5, 'public', "
            "ARRAY[]::uuid[])"
        ),
        {"id": uuid.uuid4(), "tenant_id": tenant_id, "document_id": document_id},
    )
    await owner_session.commit()
    try:
        yield tenant_id
    finally:
        await owner_session.execute(text("DELETE FROM tenants WHERE id = :id"), {"id": tenant_id})
        await owner_session.commit()


@pytest.fixture
async def app_sessionmaker(settings: Settings) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(settings.database_url)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def test_app_role_sees_only_its_own_tenant(
    app_sessionmaker: async_sessionmaker[AsyncSession],
    demo_tenant: Tenant,
    other_tenant: uuid.UUID,
) -> None:
    async with tenant_session(app_sessionmaker, demo_tenant.id) as session:
        visible_tenants = (
            (await session.execute(text("SELECT DISTINCT tenant_id FROM chunks"))).scalars().all()
        )
        leaked = (
            await session.execute(
                text("SELECT count(*) FROM chunks WHERE tenant_id = :id"), {"id": other_tenant}
            )
        ).scalar_one()

    assert visible_tenants == [demo_tenant.id]
    assert leaked == 0


async def test_app_role_sees_nothing_without_a_tenant_context(
    app_sessionmaker: async_sessionmaker[AsyncSession], other_tenant: uuid.UUID
) -> None:
    # Forgetting to scope the session must fail closed, not expose every tenant.
    async with app_sessionmaker() as session:
        count = (await session.execute(text("SELECT count(*) FROM chunks"))).scalar_one()

    assert count == 0


async def test_tenant_context_does_not_leak_between_transactions(
    app_sessionmaker: async_sessionmaker[AsyncSession],
    demo_tenant: Tenant,
    other_tenant: uuid.UUID,
) -> None:
    """`set_config(..., true)` is transaction-local, so a pooled connection cannot carry
    one tenant's context into the next request."""
    async with tenant_session(app_sessionmaker, demo_tenant.id) as session:
        assert (await session.execute(text("SELECT count(*) FROM chunks"))).scalar_one() > 0

    async with app_sessionmaker() as session:
        setting = (
            await session.execute(text("SELECT current_setting('harbor.tenant_id', true)"))
        ).scalar_one()
        assert setting in (None, "")
