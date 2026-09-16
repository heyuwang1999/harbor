"""Retrieval playground: the same pipeline as chat, without the model.

Useful for debugging an answer, for the demo's trace panel, and as the surface the
retrieval eval harness scores in M1b.
"""

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from harbor_api.api.deps import ScopeDep, SessionmakerDep, SettingsDep
from harbor_api.db.session import tenant_session
from harbor_api.llm.registry import embedding_client
from harbor_api.retrieval.search import hybrid_search

router = APIRouter(prefix="/v1", tags=["search"])


class SearchBody(BaseModel):
    query: str = Field(min_length=1)


@router.post("/search")
async def search(
    body: SearchBody, scope: ScopeDep, settings: SettingsDep, sessionmaker: SessionmakerDep
) -> dict[str, Any]:
    async with tenant_session(sessionmaker, scope.tenant_id) as session:
        result = await hybrid_search(
            session,
            scope=scope,
            query=body.query,
            settings=settings,
            embedder=embedding_client(settings),
        )
    return {
        "query": result.query,
        "normalized_query": result.normalized_query,
        "answerable": result.answerable,
        "strategy": result.strategy,
        "timings_ms": result.timings_ms,
        "context": [candidate.as_trace() for candidate in result.context],
        "candidates": [candidate.as_trace() for candidate in result.candidates],
    }
