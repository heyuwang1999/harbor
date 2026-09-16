import asyncio
import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from harbor_api.core.config import Settings, get_settings
from harbor_api.core.resources import Resources
from harbor_api.retrieval.access import ROLE_GROUPS, AccessScope, scope_for_role


@dataclass(frozen=True)
class TenantContext:
    tenant_id: uuid.UUID
    slug: str
    name: str
    groups: dict[str, uuid.UUID]


_lock = asyncio.Lock()


def get_resources(request: Request) -> Resources:
    resources: Resources = request.app.state.resources
    return resources


async def get_tenant_context(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    resources: Annotated[Resources, Depends(get_resources)],
) -> TenantContext:
    """Resolve the demo tenant once per process.

    In M2 this comes from the authenticated session instead, and everything downstream
    (scopes, RLS, retrieval filters) stays exactly as it is.
    """
    cached: TenantContext | None = getattr(request.app.state, "tenant_context", None)
    if cached is not None:
        return cached

    async with _lock:
        retry: TenantContext | None = getattr(request.app.state, "tenant_context", None)
        if retry is not None:
            return retry
        async with resources.sessionmaker() as session:
            row = (
                await session.execute(
                    text("SELECT id, slug, name FROM tenants WHERE slug = :slug"),
                    {"slug": settings.demo_tenant_slug},
                )
            ).one_or_none()
            if row is None:
                raise HTTPException(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=f"demo tenant '{settings.demo_tenant_slug}' not seeded",
                )
            # groups is tenant-scoped, so the lookup needs the RLS context set.
            await session.execute(
                text("SELECT set_config('harbor.tenant_id', :tenant_id, false)"),
                {"tenant_id": str(row.id)},
            )
            groups = {
                group.key: group.id
                for group in (
                    await session.execute(
                        text("SELECT key, id FROM groups WHERE tenant_id = CAST(:t AS uuid)"),
                        {"t": str(row.id)},
                    )
                ).all()
            }
        context = TenantContext(tenant_id=row.id, slug=row.slug, name=row.name, groups=groups)
        request.app.state.tenant_context = context
        return context


async def get_scope(
    tenant: Annotated[TenantContext, Depends(get_tenant_context)],
    settings: Annotated[Settings, Depends(get_settings)],
    x_demo_role: Annotated[str | None, Header()] = None,
) -> AccessScope:
    """Demo-only role switch. M2 replaces this header with Better Auth membership."""
    role = (x_demo_role or "visitor").lower()
    if role not in ROLE_GROUPS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"unknown role '{role}'")
    if not settings.demo_mode and role != "visitor":
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="role header requires demo mode")
    return scope_for_role(tenant.tenant_id, role, tenant.groups)


def get_sessionmaker(
    resources: Annotated[Resources, Depends(get_resources)],
) -> async_sessionmaker[AsyncSession]:
    return resources.sessionmaker


SettingsDep = Annotated[Settings, Depends(get_settings)]
ResourcesDep = Annotated[Resources, Depends(get_resources)]
TenantDep = Annotated[TenantContext, Depends(get_tenant_context)]
ScopeDep = Annotated[AccessScope, Depends(get_scope)]
SessionmakerDep = Annotated[async_sessionmaker[AsyncSession], Depends(get_sessionmaker)]
