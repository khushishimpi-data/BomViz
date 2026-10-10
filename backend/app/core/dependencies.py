"""FastAPI dependencies shared across routers."""

from typing import Annotated

from fastapi import Depends, Request

from app.db.session import ping_database
from app.services.health_service import HealthService


def get_health_service(request: Request) -> HealthService:
    state = request.app.state
    return HealthService(
        checks={
            "database": lambda: ping_database(state.engine),
            "redis": state.redis.ping,
            "storage": state.storage.check_bucket,
        },
        timeout_seconds=state.settings.readiness_timeout_seconds,
    )


HealthServiceDep = Annotated[HealthService, Depends(get_health_service)]
