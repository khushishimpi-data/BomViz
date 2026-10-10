"""Schemas shared by every endpoint."""

from typing import Any

from pydantic import BaseModel


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[dict[str, Any]]
    request_id: str | None = None


class ErrorResponse(BaseModel):
    """The standard error envelope returned for every non-2xx response."""

    error: ErrorBody
