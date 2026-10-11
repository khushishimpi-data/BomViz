"""Request and response bodies for `/auth/*`.

Request models reject unknown fields. Response models are built from ORM rows and never carry
secrets: `UserResponse` has no `password_hash`, and the refresh token travels only in its cookie.
"""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints

from app.models import UserRole
from app.services.auth_service import (
    FULL_NAME_MAX_LENGTH,
    PASSWORD_MAX_LENGTH,
    PASSWORD_MIN_LENGTH,
)

FullName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=FULL_NAME_MAX_LENGTH)
]


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    # The "must not equal the email" rule is enforced by the service.
    password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)
    full_name: FullName


class LoginRequest(BaseModel):
    """No password policy on login: any wrong password is just a failed attempt."""

    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    full_name: str
    is_active: bool
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 - OAuth token type, not a secret
    expires_in: int = Field(description="Seconds until the access token expires.")


class LoginResponse(TokenResponse):
    user: UserResponse


class MembershipResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    workspace_id: uuid.UUID
    workspace_name: str
    workspace_slug: str
    role: UserRole


class MeResponse(UserResponse):
    memberships: list[MembershipResponse]
