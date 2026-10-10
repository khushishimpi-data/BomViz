from fastapi import FastAPI
from httpx import AsyncClient

from app import __version__
from app.core.dependencies import get_health_service
from app.services.health_service import HealthService


async def _passing() -> None:
    return None


async def _failing() -> None:
    raise ConnectionError("connection refused by 10.0.0.5:5432")


async def test_liveness_reports_ok_and_version(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


async def test_readiness_ok_when_all_dependencies_pass(app: FastAPI, client: AsyncClient) -> None:
    app.dependency_overrides[get_health_service] = lambda: HealthService(
        {"database": _passing, "redis": _passing, "storage": _passing}, timeout_seconds=1
    )

    response = await client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "checks": {"database": "ok", "redis": "ok", "storage": "ok"},
    }


async def test_readiness_degraded_without_leaking_error_details(
    app: FastAPI, client: AsyncClient
) -> None:
    app.dependency_overrides[get_health_service] = lambda: HealthService(
        {"database": _failing, "redis": _passing, "storage": _passing}, timeout_seconds=1
    )

    response = await client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["checks"]["database"] == "error"
    assert "10.0.0.5" not in response.text


async def test_openapi_is_served_under_api_prefix(client: AsyncClient) -> None:
    response = await client.get("/api/v1/openapi.json")

    assert response.status_code == 200
    assert "/health/ready" in response.json()["paths"]
