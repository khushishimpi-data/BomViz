"""Workspaces the caller belongs to, and management of their members.

Routes stay thin: resolve access (`require_workspace_permission`), one `WorkspaceService` call,
shape the response. The workspace in the path is only ever reached through the caller's
membership, so a workspace they do not belong to is a 404.
"""

import uuid
from http import HTTPStatus
from typing import Any

from fastapi import APIRouter, Response, status

from app.core.dependencies import (
    CurrentUserDep,
    RateLimiterDep,
    WorkspaceManageMembersDep,
    WorkspaceReadDep,
    WorkspaceServiceDep,
)
from app.repositories.workspace_repository import WorkspaceWithRole
from app.schemas.common import ErrorResponse
from app.schemas.pagination import Page, PaginationDep
from app.schemas.workspace import (
    MemberAddRequest,
    MemberResponse,
    MemberRoleUpdateRequest,
    WorkspaceCreateRequest,
    WorkspaceResponse,
)

router = APIRouter(prefix="/workspaces", tags=["workspaces"])

# (limit, window seconds) per acting user. Adding by email answers "does this account exist?"
# (404 `USER_NOT_FOUND`), so the endpoint is capped to keep it from being an enumeration oracle.
MEMBER_ADD_RATE_LIMIT = (30, 3600)

_ERROR_DESCRIPTIONS: dict[int, str] = {
    HTTPStatus.UNAUTHORIZED: "Not authenticated, or the access token was rejected.",
    HTTPStatus.FORBIDDEN: "Your role in this workspace does not allow this action.",
    HTTPStatus.NOT_FOUND: "The workspace, member or user was not found.",
    HTTPStatus.CONFLICT: "The request conflicts with existing data.",
    HTTPStatus.UNPROCESSABLE_ENTITY: "The request failed validation.",
    HTTPStatus.TOO_MANY_REQUESTS: "Too many attempts; retry after `Retry-After` seconds.",
}


def _error_responses(*status_codes: int) -> dict[int | str, dict[str, Any]]:
    """OpenAPI entries documenting the standard error envelope for `status_codes`."""
    return {
        code: {"model": ErrorResponse, "description": _ERROR_DESCRIPTIONS[code]}
        for code in status_codes
    }


def _workspace_response(item: WorkspaceWithRole) -> WorkspaceResponse:
    workspace = item.workspace
    return WorkspaceResponse(
        id=workspace.id,
        name=workspace.name,
        slug=workspace.slug,
        owner_id=workspace.owner_id,
        role=item.role,
        created_at=workspace.created_at,
    )


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=WorkspaceResponse,
    responses=_error_responses(
        HTTPStatus.UNAUTHORIZED, HTTPStatus.CONFLICT, HTTPStatus.UNPROCESSABLE_ENTITY
    ),
)
async def create_workspace(
    body: WorkspaceCreateRequest, current_user: CurrentUserDep, service: WorkspaceServiceDep
) -> WorkspaceResponse:
    """Create a workspace; the caller becomes its owner and ADMIN."""
    created = await service.create(current_user, body.name, body.slug)
    return _workspace_response(created)


@router.get(
    "",
    response_model=Page[WorkspaceResponse],
    responses=_error_responses(HTTPStatus.UNAUTHORIZED, HTTPStatus.UNPROCESSABLE_ENTITY),
)
async def list_workspaces(
    current_user: CurrentUserDep, service: WorkspaceServiceDep, pagination: PaginationDep
) -> Page[WorkspaceResponse]:
    """The workspaces the caller belongs to, ordered by name."""
    items, total = await service.list_for_user(
        current_user, limit=pagination.limit, offset=pagination.offset
    )
    return Page(
        items=[_workspace_response(item) for item in items],
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.get(
    "/{workspace_id}",
    response_model=WorkspaceResponse,
    responses=_error_responses(HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN, HTTPStatus.NOT_FOUND),
)
async def get_workspace(access: WorkspaceReadDep) -> WorkspaceResponse:
    """One workspace the caller belongs to, with their role in it."""
    return _workspace_response(
        WorkspaceWithRole(workspace=access.workspace, role=access.membership.role)
    )


@router.get(
    "/{workspace_id}/members",
    response_model=Page[MemberResponse],
    responses=_error_responses(
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.FORBIDDEN,
        HTTPStatus.NOT_FOUND,
        HTTPStatus.UNPROCESSABLE_ENTITY,
    ),
)
async def list_members(
    access: WorkspaceReadDep, service: WorkspaceServiceDep, pagination: PaginationDep
) -> Page[MemberResponse]:
    """Members of the workspace, ordered by full name then email."""
    items, total = await service.list_members(
        access, limit=pagination.limit, offset=pagination.offset
    )
    return Page(
        items=[MemberResponse.model_validate(item) for item in items],
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.post(
    "/{workspace_id}/members",
    status_code=status.HTTP_201_CREATED,
    response_model=MemberResponse,
    responses=_error_responses(
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.FORBIDDEN,
        HTTPStatus.NOT_FOUND,
        HTTPStatus.CONFLICT,
        HTTPStatus.UNPROCESSABLE_ENTITY,
        HTTPStatus.TOO_MANY_REQUESTS,
    ),
)
async def add_member(
    body: MemberAddRequest,
    access: WorkspaceManageMembersDep,
    current_user: CurrentUserDep,
    service: WorkspaceServiceDep,
    rate_limiter: RateLimiterDep,
) -> MemberResponse:
    """Add an existing account to the workspace by email (30 attempts per hour per caller)."""
    await rate_limiter.hit("member_add", str(current_user.id), *MEMBER_ADD_RATE_LIMIT)
    member = await service.add_member(access, current_user, body.email, body.role)
    return MemberResponse.model_validate(member)


@router.patch(
    "/{workspace_id}/members/{member_id}",
    response_model=MemberResponse,
    responses=_error_responses(
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.FORBIDDEN,
        HTTPStatus.NOT_FOUND,
        HTTPStatus.CONFLICT,
        HTTPStatus.UNPROCESSABLE_ENTITY,
    ),
)
async def change_member_role(
    member_id: uuid.UUID,
    body: MemberRoleUpdateRequest,
    access: WorkspaceManageMembersDep,
    current_user: CurrentUserDep,
    service: WorkspaceServiceDep,
) -> MemberResponse:
    """Change a member's role. The last ADMIN cannot be demoted."""
    member = await service.change_role(access, current_user, member_id, body.role)
    return MemberResponse.model_validate(member)


@router.delete(
    "/{workspace_id}/members/{member_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=_error_responses(
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.FORBIDDEN,
        HTTPStatus.NOT_FOUND,
        HTTPStatus.CONFLICT,
        HTTPStatus.UNPROCESSABLE_ENTITY,
    ),
)
async def remove_member(
    member_id: uuid.UUID,
    access: WorkspaceManageMembersDep,
    current_user: CurrentUserDep,
    service: WorkspaceServiceDep,
) -> Response:
    """Remove a membership (never the account). The last ADMIN cannot be removed."""
    await service.remove_member(access, current_user, member_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
