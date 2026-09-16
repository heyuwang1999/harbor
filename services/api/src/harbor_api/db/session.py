import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# Row-Level Security policies read this setting; `set_config(..., true)` scopes it to
# the current transaction, so a pooled connection can never leak it to another tenant.
_SET_TENANT = text("SELECT set_config('harbor.tenant_id', :tenant_id, true)")


@asynccontextmanager
async def tenant_session(
    sessionmaker: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID
) -> AsyncIterator[AsyncSession]:
    """Open a transaction scoped to one tenant.

    Every request path must use this. Queries still filter by tenant_id explicitly
    (indexes, clarity); RLS is the backstop for code that forgets.
    """
    async with sessionmaker() as session, session.begin():
        await session.execute(_SET_TENANT, {"tenant_id": str(tenant_id)})
        yield session
