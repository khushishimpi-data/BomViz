"""Domain exceptions and the handlers that render them as the standard error envelope.

Every error response has the shape::

    {"error": {"code": "...", "message": "...", "details": [...], "request_id": "..."}}
"""

import logging
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import request_id_ctx

logger = logging.getLogger(__name__)

_STATUS_CODES: dict[int, str] = {
    HTTPStatus.BAD_REQUEST: "BAD_REQUEST",
    HTTPStatus.UNAUTHORIZED: "UNAUTHENTICATED",
    HTTPStatus.FORBIDDEN: "FORBIDDEN",
    HTTPStatus.NOT_FOUND: "NOT_FOUND",
    HTTPStatus.METHOD_NOT_ALLOWED: "METHOD_NOT_ALLOWED",
    HTTPStatus.CONFLICT: "CONFLICT",
    HTTPStatus.REQUEST_ENTITY_TOO_LARGE: "PAYLOAD_TOO_LARGE",
    HTTPStatus.UNPROCESSABLE_ENTITY: "VALIDATION_ERROR",
    HTTPStatus.TOO_MANY_REQUESTS: "RATE_LIMITED",
}


class AppError(Exception):
    """Base class for errors that are safe to show to API clients."""

    status_code: int = HTTPStatus.BAD_REQUEST
    code: str = "BAD_REQUEST"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        self.details = details or []


def error_response(
    status_code: int, code: str, message: str, details: list[dict[str, Any]] | None = None
) -> JSONResponse:
    """Build a JSON response in the standard error envelope."""
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "details": details or [],
                "request_id": request_id_ctx.get(),
            }
        },
    )


async def _handle_app_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)  # noqa: S101 - narrowing for the type checker
    return error_response(exc.status_code, exc.code, exc.message, exc.details)


async def _handle_http_exception(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)  # noqa: S101
    code = _STATUS_CODES.get(exc.status_code, "HTTP_ERROR")
    message = exc.detail if isinstance(exc.detail, str) else HTTPStatus(exc.status_code).phrase
    return error_response(exc.status_code, code, message)


async def _handle_validation_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)  # noqa: S101
    details = [
        {
            "field": ".".join(str(part) for part in error["loc"]),
            "message": error["msg"],
            "type": error["type"],
        }
        for error in exc.errors()
    ]
    return error_response(
        HTTPStatus.UNPROCESSABLE_ENTITY, "VALIDATION_ERROR", "Request validation failed.", details
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Install envelope-producing handlers. Unhandled exceptions are handled in middleware."""
    app.add_exception_handler(AppError, _handle_app_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
