from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from harbor_api.api import health, meta
from harbor_api.core.config import Settings, get_settings
from harbor_api.core.logging import configure_logging
from harbor_api.core.resources import Resources


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.resources = Resources.create(settings)
        try:
            yield
        finally:
            await app.state.resources.close()

    app = FastAPI(title="Harbor API", version=settings.version, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router)
    app.include_router(meta.router)
    return app
