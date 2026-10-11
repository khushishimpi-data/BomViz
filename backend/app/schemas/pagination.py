"""Offset pagination shared by every collection endpoint.

Responses are `{items, total, limit, offset}` (project convention, see docs/API.md).
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Query
from pydantic import BaseModel

DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 200


class Page[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int


@dataclass(frozen=True, slots=True)
class PaginationParams:
    limit: int
    offset: int


def get_pagination_params(
    limit: Annotated[
        int, Query(ge=1, le=MAX_PAGE_LIMIT, description="Maximum number of items to return.")
    ] = DEFAULT_PAGE_LIMIT,
    offset: Annotated[int, Query(ge=0, description="Number of items to skip.")] = 0,
) -> PaginationParams:
    return PaginationParams(limit=limit, offset=offset)


PaginationDep = Annotated[PaginationParams, Depends(get_pagination_params)]
