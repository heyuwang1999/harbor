from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from harbor_api.api.health import get_readiness_checks
from harbor_api.app import create_app
from harbor_api.core.config import Settings
from harbor_api.core.resources import HealthCheck


async def ok() -> None:
    return None


async def boom() -> None:
    raise ConnectionError("down")


@pytest.fixture
def app() -> FastAPI:
    return create_app(Settings(env="test", log_json=False, readiness_timeout_s=0.5))


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    # ASGITransport skips the lifespan, so no real DB/Redis connections are opened.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


def use_checks(app: FastAPI, checks: dict[str, HealthCheck]) -> None:
    app.dependency_overrides[get_readiness_checks] = lambda: checks


async def test_healthz(client: AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readyz_all_ok(app: FastAPI, client: AsyncClient) -> None:
    use_checks(app, {"database": ok, "redis": ok})
    response = await client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": "ok", "redis": "ok"}}


async def test_readyz_reports_failed_dependency(app: FastAPI, client: AsyncClient) -> None:
    use_checks(app, {"database": ok, "redis": boom})
    response = await client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["checks"] == {"database": "ok", "redis": "fail"}


async def test_readyz_times_out_slow_dependency(app: FastAPI, client: AsyncClient) -> None:
    import asyncio

    async def hang() -> None:
        await asyncio.sleep(5)

    use_checks(app, {"database": hang})
    response = await client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["checks"] == {"database": "fail"}


async def test_meta(client: AsyncClient) -> None:
    response = await client.get("/v1/meta")
    assert response.status_code == 200
    assert response.json()["name"] == "harbor"
