"""Celery worker for ingestion.

Parsing and embedding a 100-page PDF takes far longer than an HTTP request should, so
uploads return immediately and the work happens here. Redis is already in the stack as the
cache and rate limiter, so it doubles as the broker rather than adding another service.

Tasks wrap the async pipeline with `asyncio.run` instead of growing a second, synchronous
database and HTTP stack — one code path stays under test.
"""

import asyncio
import uuid

import structlog
from celery import Celery
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harbor_api.core.config import get_settings
from harbor_api.core.logging import configure_logging
from harbor_api.ingestion.service import ingest_document

log = structlog.get_logger(__name__)
settings = get_settings()
configure_logging(settings.log_level, settings.log_json)

celery_app = Celery("harbor", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_acks_late=True,  # a crashed worker must not lose the document
    worker_prefetch_multiplier=1,  # ingestion is slow and uneven; do not hoard tasks
    task_time_limit=900,
    task_soft_time_limit=840,
    task_always_eager=settings.ingest_eager,  # inline in tests and the demo seed
)


@celery_app.task(name="harbor.ingest_document", bind=True, max_retries=2)
def ingest_document_task(self, tenant_id: str, document_id: str) -> int:  # type: ignore[no-untyped-def]
    async def run() -> int:
        engine = create_async_engine(settings.database_url)
        try:
            sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
            return await ingest_document(
                sessionmaker,
                tenant_id=uuid.UUID(tenant_id),
                document_id=uuid.UUID(document_id),
                settings=settings,
            )
        finally:
            await engine.dispose()

    try:
        return asyncio.run(run())
    except Exception as error:  # transient: provider hiccup, database restart
        log.error("ingestion.task_failed", document_id=document_id, error=str(error))
        raise self.retry(exc=error, countdown=10) from error
