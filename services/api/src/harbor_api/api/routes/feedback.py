import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from harbor_api.api.deps import ScopeDep, SessionmakerDep
from harbor_api.db.session import tenant_session

router = APIRouter(prefix="/v1", tags=["feedback"])


class FeedbackBody(BaseModel):
    message_id: uuid.UUID
    rating: Literal[-1, 1]
    reason: str | None = None
    comment: str | None = None


@router.post("/feedback", status_code=status.HTTP_201_CREATED)
async def submit_feedback(
    body: FeedbackBody, scope: ScopeDep, sessionmaker: SessionmakerDep
) -> dict[str, str]:
    """Thumbs up/down. In M1b these rows label the eval set and drive the content-gap report."""
    async with tenant_session(sessionmaker, scope.tenant_id) as session:
        try:
            await session.execute(
                text(
                    "INSERT INTO feedback (id, tenant_id, message_id, rating, reason, comment) "
                    "VALUES (gen_random_uuid(), CAST(:tenant_id AS uuid), "
                    "CAST(:message_id AS uuid), :rating, :reason, :comment)"
                ),
                {
                    "tenant_id": str(scope.tenant_id),
                    "message_id": str(body.message_id),
                    "rating": body.rating,
                    "reason": body.reason,
                    "comment": body.comment,
                },
            )
        except IntegrityError as error:  # unknown or other-tenant message
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="message not found") from error
    return {"status": "recorded"}
