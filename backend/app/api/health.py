"""Unversioned liveness and readiness probes used by Docker, load balancers and the UI."""

from fastapi import APIRouter, Response, status

from app import __version__
from app.core.dependencies import HealthServiceDep
from app.schemas.health import LivenessResponse, ReadinessResponse

router = APIRouter(prefix="/health", tags=["health"])


@router.get("", response_model=LivenessResponse)
async def liveness() -> LivenessResponse:
    """The process is up and serving requests."""
    return LivenessResponse(status="ok", version=__version__)


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadinessResponse}},
)
async def readiness(health_service: HealthServiceDep, response: Response) -> ReadinessResponse:
    """Database, Redis and object storage are all reachable."""
    results = await health_service.readiness()
    all_ok = all(results.values())
    if not all_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(
        status="ok" if all_ok else "degraded",
        checks={name: "ok" if passed else "error" for name, passed in results.items()},
    )
