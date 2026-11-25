from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, Response

from chainsight_api.health import Probe, get_readiness_probes, overall_status
from chainsight_api.schemas.health import LivenessResponse, ReadinessResponse
from core.settings import get_settings
from core.timeutils import utcnow

router = APIRouter(prefix="/health", tags=["health"])


@router.get("", response_model=LivenessResponse, summary="Liveness probe")
async def liveness() -> LivenessResponse:
    s = get_settings()
    return LivenessResponse(
        service=s.app_name, version=s.app_version, environment=s.app_env.value, time=utcnow()
    )


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    summary="Readiness probe (database, extensions, migrations, Redis)",
    responses={503: {"model": ReadinessResponse, "description": "A dependency is down"}},
)
async def readiness(
    response: Response, probes: Annotated[list[Probe], Depends(get_readiness_probes)]
) -> ReadinessResponse:
    components = list(await asyncio.gather(*(p() for p in probes)))
    status = overall_status(components)
    if status == "error":
        response.status_code = 503
    return ReadinessResponse(status=status, time=utcnow(), components=components)
