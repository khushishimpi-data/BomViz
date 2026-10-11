"""Request and response bodies for `/workspaces/*`.

Request models reject unknown fields. Identity and tenant never come from these bodies: the
caller is the authenticated user and the workspace is the path id checked against membership.
"""

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, EmailStr, StringConstraints

from app.models import UserRole
from app.services.workspace_service import (
    SLUG_MAX_LENGTH,
    SLUG_MIN_LENGTH,
    SLUG_PATTERN,
    WORKSPACE_NAME_MAX_LENGTH,
)

WorkspaceName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=WORKSPACE_NAME_MAX_LENGTH),
]
Slug = Annotated[
    str,
    StringConstraints(min_length=SLUG_MIN_LENGTH, max_length=SLUG_MAX_LENGTH, pattern=SLUG_PATTERN),
]


class WorkspaceCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: WorkspaceName
    slug: Slug | None = None


class WorkspaceResponse(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    owner_id: uuid.UUID
    role: UserRole
    created_at: datetime


class MemberAddRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    role: UserRole


class MemberRoleUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: UserRole


class MemberResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    email: str
    full_name: str
    role: UserRole
    created_at: datetime
