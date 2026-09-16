import asyncio
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel

from harbor_api.core.config import Settings, get_settings
from harbor_api.core.resources import HealthCheck

router = APIRouter(tags=["health"])


class ReadinessReport(BaseModel):
    status: Literal["ok", "degraded"]
    checks: dict[str, Literal["ok", "fail"]]


def get_readiness_checks(request: Request) -> dict[str, HealthCheck]:
    checks: dict[str, HealthCheck] = request.app.state.resources.readiness_checks()
    return checks


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness: the process is up. Never touches dependencies."""
    return {"status": "ok"}


@router.get("/readyz", response_model=ReadinessReport)
async def readyz(
    response: Response,
    checks: Annotated[dict[str, HealthCheck], Depends(get_readiness_checks)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ReadinessReport:
    """Readiness: dependencies reachable. Kubernetes stops routing traffic on 503."""

    async def run(check: HealthCheck) -> Literal["ok", "fail"]:
        try:
            await asyncio.wait_for(check(), timeout=settings.readiness_timeout_s)
        except Exception:
            return "fail"
        return "ok"

    results = await asyncio.gather(*(run(check) for check in checks.values()))
    report = dict(zip(checks.keys(), results, strict=True))
    healthy = all(result == "ok" for result in results)
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessReport(status="ok" if healthy else "degraded", checks=report)
