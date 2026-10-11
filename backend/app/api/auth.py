"""Registration, sign-in, refresh-token rotation, sign-out and the current user.

Routes stay thin: rate-limit, one `AuthService` call, shape the response. The refresh token is
only ever sent and received as an HttpOnly cookie scoped to `settings.refresh_cookie_path`.
"""

import math
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Any

from fastapi import APIRouter, Request, Response, status

from app.core.config import Settings
from app.core.dependencies import (
    AuthServiceDep,
    ClientIpDep,
    CurrentUserDep,
    RateLimiterDep,
    SettingsDep,
)
from app.core.exceptions import AppError
from app.core.security import TokenExpiredError, TokenInvalidError
from app.schemas.auth import (
    LoginRequest,
    LoginResponse,
    MembershipResponse,
    MeResponse,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)
from app.schemas.common import ErrorResponse
from app.services.auth_service import TokenReusedError

router = APIRouter(prefix="/auth", tags=["auth"])

# (limit, window seconds) per client IP, per the Auth flow design.
REGISTER_RATE_LIMIT = (5, 3600)
LOGIN_IP_RATE_LIMIT = (30, 900)
REFRESH_RATE_LIMIT = (60, 900)

_ERROR_DESCRIPTIONS: dict[int, str] = {
    HTTPStatus.UNAUTHORIZED: "Not authenticated, or the credentials or token were rejected.",
    HTTPStatus.CONFLICT: "The request conflicts with existing data.",
    HTTPStatus.UNPROCESSABLE_ENTITY: "The request body failed validation.",
    HTTPStatus.TOO_MANY_REQUESTS: "Too many attempts; see the `Retry-After` header.",
}


def _error_responses(*status_codes: int) -> dict[int | str, dict[str, Any]]:
    """OpenAPI entries documenting the standard error envelope for `status_codes`."""
    return {
        code: {"model": ErrorResponse, "description": _ERROR_DESCRIPTIONS[code]}
        for code in status_codes
    }


class MissingRefreshTokenError(AppError):
    status_code = HTTPStatus.UNAUTHORIZED
    code = "UNAUTHENTICATED"

    def __init__(self) -> None:
        super().__init__("No active session.")


def set_refresh_cookie(
    response: Response, settings: Settings, refresh_token: str, expires_at: datetime
) -> None:
    # Sessions have an absolute lifetime, so the cookie lives only as long as the session left.
    remaining = math.ceil((expires_at - datetime.now(UTC)).total_seconds())
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=refresh_token,
        max_age=max(remaining, 0),
        path=settings.refresh_cookie_path,
        secure=settings.refresh_cookie_secure,
        httponly=True,
        samesite="strict",
    )


def clear_refresh_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.refresh_cookie_name,
        path=settings.refresh_cookie_path,
        secure=settings.refresh_cookie_secure,
        httponly=True,
        samesite="strict",
    )


def _clear_refresh_cookie_header(settings: Settings) -> str:
    """The `Set-Cookie` value that clears the refresh cookie, for attaching to an `AppError`."""
    scratch = Response()
    clear_refresh_cookie(scratch, settings)
    return scratch.headers["set-cookie"]


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    response_model=UserResponse,
    responses=_error_responses(
        HTTPStatus.CONFLICT, HTTPStatus.UNPROCESSABLE_ENTITY, HTTPStatus.TOO_MANY_REQUESTS
    ),
)
async def register(
    body: RegisterRequest,
    auth_service: AuthServiceDep,
    rate_limiter: RateLimiterDep,
    client_ip: ClientIpDep,
) -> UserResponse:
    """Create an account. Does not sign in."""
    await rate_limiter.hit("register", client_ip, *REGISTER_RATE_LIMIT)
    user = await auth_service.register(body.email, body.password, body.full_name)
    return UserResponse.model_validate(user)


@router.post(
    "/login",
    response_model=LoginResponse,
    responses=_error_responses(
        HTTPStatus.UNAUTHORIZED, HTTPStatus.UNPROCESSABLE_ENTITY, HTTPStatus.TOO_MANY_REQUESTS
    ),
)
async def login(
    *,
    body: LoginRequest,
    response: Response,
    auth_service: AuthServiceDep,
    rate_limiter: RateLimiterDep,
    client_ip: ClientIpDep,
    settings: SettingsDep,
) -> LoginResponse:
    """Exchange credentials for an access token and a refresh cookie."""
    await rate_limiter.hit("login_ip", client_ip, *LOGIN_IP_RATE_LIMIT)
    result = await auth_service.login(body.email, body.password, client_ip)
    set_refresh_cookie(response, settings, result.refresh_token_raw, result.refresh_expires_at)
    return LoginResponse(
        access_token=result.access_token,
        expires_in=result.expires_in,
        user=UserResponse.model_validate(result.user),
    )


@router.post(
    "/refresh",
    response_model=TokenResponse,
    responses=_error_responses(HTTPStatus.UNAUTHORIZED, HTTPStatus.TOO_MANY_REQUESTS),
)
async def refresh(
    *,
    request: Request,
    response: Response,
    auth_service: AuthServiceDep,
    rate_limiter: RateLimiterDep,
    client_ip: ClientIpDep,
    settings: SettingsDep,
) -> TokenResponse:
    """Rotate the refresh cookie and issue a new access token.

    Any rejected cookie (unknown, expired, reused) is cleared in the error response.
    """
    await rate_limiter.hit("refresh", client_ip, *REFRESH_RATE_LIMIT)
    raw_token = request.cookies.get(settings.refresh_cookie_name)
    if not raw_token:
        raise MissingRefreshTokenError()
    try:
        result = await auth_service.refresh(raw_token)
    except (TokenInvalidError, TokenExpiredError, TokenReusedError) as exc:
        exc.headers = {**exc.headers, "Set-Cookie": _clear_refresh_cookie_header(settings)}
        raise
    set_refresh_cookie(response, settings, result.refresh_token_raw, result.refresh_expires_at)
    return TokenResponse(access_token=result.access_token, expires_in=result.expires_in)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, auth_service: AuthServiceDep, settings: SettingsDep) -> Response:
    """End the cookie's session. Idempotent; no access token required."""
    await auth_service.logout(request.cookies.get(settings.refresh_cookie_name))
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_refresh_cookie(response, settings)
    return response


@router.get(
    "/me",
    response_model=MeResponse,
    responses=_error_responses(HTTPStatus.UNAUTHORIZED),
)
async def me(current_user: CurrentUserDep, auth_service: AuthServiceDep) -> MeResponse:
    """The signed-in user and the workspaces they belong to."""
    result = await auth_service.get_me(current_user)
    user = UserResponse.model_validate(result.user)
    return MeResponse(
        **user.model_dump(),
        memberships=[MembershipResponse.model_validate(m) for m in result.memberships],
    )
