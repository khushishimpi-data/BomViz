"""Products of a workspace, and their revisions.

Routes stay thin: resolve access, one `ProductService` call, shape the response. A workspace is
resolved through `require_workspace_permission`; a product through `require_product_permission`,
which reaches it only via the caller's membership of its workspace. Either way, something the
caller cannot see is a 404, and a role lacking the permission is a 403.
"""

import uuid
from collections.abc import Awaitable, Callable
from http import HTTPStatus
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import CurrentUserDep, DbSessionDep, require_workspace_permission
from app.core.permissions import Permission, has_permission
from app.models import ProductStatus, User
from app.repositories.product_repository import ProductAccess, ProductRepository
from app.repositories.workspace_repository import WorkspaceAccess
from app.schemas.common import ErrorResponse
from app.schemas.pagination import Page, PaginationDep
from app.schemas.product import (
    ProductCreateRequest,
    ProductDetailResponse,
    ProductResponse,
    RevisionCreateRequest,
    RevisionResponse,
)
from app.services.product_service import (
    SEARCH_MAX_LENGTH,
    ProductNotFoundError,
    ProductService,
)
from app.services.workspace_service import ForbiddenError

router = APIRouter(tags=["products"])

_ERROR_DESCRIPTIONS: dict[int, str] = {
    HTTPStatus.UNAUTHORIZED: "Not authenticated, or the access token was rejected.",
    HTTPStatus.FORBIDDEN: "Your role in this workspace does not allow this action.",
    HTTPStatus.NOT_FOUND: "The workspace or product was not found.",
    HTTPStatus.CONFLICT: "The code is already in use.",
    HTTPStatus.UNPROCESSABLE_ENTITY: "The request failed validation.",
}


def _error_responses(*status_codes: int) -> dict[int | str, dict[str, Any]]:
    """OpenAPI entries documenting the standard error envelope for `status_codes`."""
    return {
        code: {"model": ErrorResponse, "description": _ERROR_DESCRIPTIONS[code]}
        for code in status_codes
    }


def get_product_service(session: DbSessionDep) -> ProductService:
    return ProductService(session)


ProductServiceDep = Annotated[ProductService, Depends(get_product_service)]


def require_product_permission(
    permission: Permission,
) -> Callable[[uuid.UUID, User, AsyncSession], Awaitable[ProductAccess]]:
    """Dependency factory resolving the `{product_id}` path param for the current user.

    The product and the caller's role come from one query joining the product's workspace
    membership for the caller. Nonexistent product or not a member -> 404 `NOT_FOUND` (the two
    are indistinguishable); a member whose role lacks `permission` -> 403 `FORBIDDEN`.
    """

    async def resolve_product_access(
        product_id: uuid.UUID, current_user: CurrentUserDep, session: DbSessionDep
    ) -> ProductAccess:
        access = await ProductRepository(session).get_access(product_id, current_user.id)
        if access is None:
            raise ProductNotFoundError()
        if not has_permission(access.role, permission):
            raise ForbiddenError()
        return access

    return resolve_product_access


WorkspaceProductReadDep = Annotated[
    WorkspaceAccess, Depends(require_workspace_permission(Permission.PRODUCT_READ))
]
WorkspaceProductWriteDep = Annotated[
    WorkspaceAccess, Depends(require_workspace_permission(Permission.PRODUCT_WRITE))
]
ProductReadDep = Annotated[
    ProductAccess, Depends(require_product_permission(Permission.PRODUCT_READ))
]
ProductWriteDep = Annotated[
    ProductAccess, Depends(require_product_permission(Permission.PRODUCT_WRITE))
]

_STANDARD_ERRORS = (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN, HTTPStatus.NOT_FOUND)


@router.post(
    "/workspaces/{workspace_id}/products",
    status_code=status.HTTP_201_CREATED,
    response_model=ProductResponse,
    responses=_error_responses(
        *_STANDARD_ERRORS, HTTPStatus.CONFLICT, HTTPStatus.UNPROCESSABLE_ENTITY
    ),
)
async def create_product(
    body: ProductCreateRequest,
    access: WorkspaceProductWriteDep,
    current_user: CurrentUserDep,
    service: ProductServiceDep,
) -> ProductResponse:
    """Create a product in the workspace. Codes are unique within a workspace."""
    product = await service.create(
        access,
        current_user,
        product_code=body.product_code,
        name=body.name,
        description=body.description,
        status=body.status,
    )
    return ProductResponse.model_validate(product)


@router.get(
    "/workspaces/{workspace_id}/products",
    response_model=Page[ProductResponse],
    responses=_error_responses(*_STANDARD_ERRORS, HTTPStatus.UNPROCESSABLE_ENTITY),
)
async def list_products(
    access: WorkspaceProductReadDep,
    service: ProductServiceDep,
    pagination: PaginationDep,
    q: Annotated[
        str | None,
        Query(
            max_length=SEARCH_MAX_LENGTH,
            description="Case-insensitive substring of the product code or name.",
        ),
    ] = None,
    product_status: Annotated[
        ProductStatus | None, Query(alias="status", description="Only products in this status.")
    ] = None,
) -> Page[ProductResponse]:
    """Products of the workspace ordered by code, optionally filtered."""
    items, total = await service.list_products(
        access,
        q=q.strip() if q else None,
        status=product_status,
        limit=pagination.limit,
        offset=pagination.offset,
    )
    return Page(
        items=[ProductResponse.model_validate(item) for item in items],
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.get(
    "/products/{product_id}",
    response_model=ProductDetailResponse,
    responses=_error_responses(*_STANDARD_ERRORS, HTTPStatus.UNPROCESSABLE_ENTITY),
)
async def get_product(access: ProductReadDep, service: ProductServiceDep) -> ProductDetailResponse:
    """One product with its revisions, newest first."""
    detail = await service.get_detail(access)
    return ProductDetailResponse(
        **ProductResponse.model_validate(detail.product).model_dump(),
        revisions=[RevisionResponse.model_validate(item) for item in detail.revisions],
    )


@router.post(
    "/products/{product_id}/revisions",
    status_code=status.HTTP_201_CREATED,
    response_model=RevisionResponse,
    responses=_error_responses(
        *_STANDARD_ERRORS, HTTPStatus.CONFLICT, HTTPStatus.UNPROCESSABLE_ENTITY
    ),
)
async def create_revision(
    body: RevisionCreateRequest,
    access: ProductWriteDep,
    current_user: CurrentUserDep,
    service: ProductServiceDep,
) -> RevisionResponse:
    """Add a revision to the product. Codes are unique within a product."""
    revision = await service.create_revision(
        access,
        current_user,
        revision_code=body.revision_code,
        description=body.description,
        status=body.status,
    )
    return RevisionResponse.model_validate(revision)
