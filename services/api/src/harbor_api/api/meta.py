from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from harbor_api.core.config import Settings, get_settings

router = APIRouter(prefix="/v1", tags=["meta"])


class Meta(BaseModel):
    name: str
    version: str
    env: str


@router.get("/meta", response_model=Meta)
async def meta(settings: Annotated[Settings, Depends(get_settings)]) -> Meta:
    return Meta(name="harbor", version=settings.version, env=settings.env)
