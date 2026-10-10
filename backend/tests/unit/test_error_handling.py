import re

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from app.core.exceptions import AppError


@pytest.fixture
def app_with_routes(app: FastAPI) -> FastAPI:
    @app.get("/test/app-error")
    async def raise_app_error() -> None:
        raise AppError(
            "BOM contains blocking validation errors.",
            code="VALIDATION_FAILED",
            status_code=409,
            details=[{"row_number": 4}],
        )

    @app.get("/test/crash")
    async def crash() -> None:
        raise RuntimeError("password=hunter2 at /srv/app/secret.py line 12")

    @app.get("/test/typed")
    async def typed(build_quantity: int) -> dict[str, int]:
        return {"build_quantity": build_quantity}

    return app


async def test_app_error_rendered_as_envelope(
    app_with_routes: FastAPI, client: AsyncClient
) -> None:
    response = await client.get("/test/app-error")

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_FAILED"
    assert error["message"] == "BOM contains blocking validation errors."
    assert error["details"] == [{"row_number": 4}]
    assert error["request_id"] == response.headers["X-Request-ID"]


async def test_unknown_route_returns_not_found_envelope(client: AsyncClient) -> None:
    response = await client.get("/does-not-exist")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_request_validation_lists_field_errors(
    app_with_routes: FastAPI, client: AsyncClient
) -> None:
    response = await client.get("/test/typed", params={"build_quantity": "lots"})

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["details"][0]["field"] == "query.build_quantity"


async def test_unhandled_exception_hides_internals(
    app_with_routes: FastAPI, client: AsyncClient
) -> None:
    response = await client.get("/test/crash")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "hunter2" not in response.text
    assert "secret.py" not in response.text


async def test_safe_incoming_request_id_is_echoed(client: AsyncClient) -> None:
    response = await client.get("/health", headers={"X-Request-ID": "trace-abc.123"})

    assert response.headers["X-Request-ID"] == "trace-abc.123"


async def test_unsafe_incoming_request_id_is_replaced(client: AsyncClient) -> None:
    response = await client.get("/health", headers={"X-Request-ID": "bad id\r\ninjected"})

    assert re.fullmatch(r"[0-9a-f]{32}", response.headers["X-Request-ID"])
