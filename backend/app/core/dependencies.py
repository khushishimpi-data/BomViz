"""FastAPI dependencies shared across routers."""

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from http import HTTPStatus
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.client_ip import client_identity, resolve_client_ip
from app.core.config import Settings
from app.core.exceptions import AppError
from app.core.permissions import Permission, has_permission
from app.db.session import ping_database
from app.models import User
from app.repositories.workspace_repository import WorkspaceAccess, WorkspaceRepository
from app.services.auth_service import AuthService
from app.services.health_service import HealthService
from app.services.rate_limiter import RateLimiter
from app.services.workspace_service import (
    ForbiddenError,
    WorkspaceNotFoundError,
    WorkspaceService,
)

# Tells clients which scheme to use after a 401 from `CurrentUserDep` (RFC 6750).
WWW_AUTHENTICATE_BEARER = {"WWW-Authenticate": "Bearer"}


def get_settings_from_app(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


SettingsDep = Annotated[Settings, Depends(get_settings_from_app)]


def get_client_ip(request: Request, settings: SettingsDep) -> str:
    """The caller's identity for rate limits and the login throttle.

    `X-Forwarded-For` is honoured only when the direct peer is in `TRUSTED_PROXY_IPS`; the
    resolved address is then normalized (IPv6 -> its /64) by `client_identity`.
    """
    peer = request.client.host if request.client else None
    # Repeated headers are one list in order (RFC 9110 5.3), so join them before walking it.
    forwarded = ",".join(request.headers.getlist("x-forwarded-for")) or None
    ip = resolve_client_ip(peer, forwarded, settings.trusted_proxy_networks)
    return client_identity(ip)


ClientIpDep = Annotated[str, Depends(get_client_ip)]


def get_health_service(request: Request) -> HealthService:
    state = request.app.state
    return HealthService(
        checks={
            "database": lambda: ping_database(state.engine),
            "redis": state.redis.ping,
            "storage": state.storage.check_bucket,
        },
        timeout_seconds=state.settings.readiness_timeout_seconds,
    )


HealthServiceDep = Annotated[HealthService, Depends(get_health_service)]


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Provide one session per request as a single unit of work.

    The request's work is committed when the handler returns and rolled back when it raises
    (including `AppError`s that become 4xx envelopes), so a failed request never leaves
    half-written rows behind.

    Work that must persist even though the request fails (failed-login audit rows, login
    throttle counters) is committed explicitly by the service with `await session.commit()`
    *before* it raises. The rollback here then only discards what was done after that commit.

    Why commit/rollback is managed by hand instead of `async with session.begin()`: inside a
    `begin()` block SQLAlchemy 2 refuses any further use of the session after an explicit
    `commit()` ("Can't operate on closed transaction inside context manager"), so a service
    could not commit an audit row and keep working. Without the block, the session autobegins
    a fresh transaction on next use, and the final commit/rollback below applies to whatever
    is still pending (a no-op when nothing is).

    `DbSessionDep` uses `scope="function"` so this cleanup runs before the response is sent:
    a failing commit surfaces as an error response instead of a success the client already
    received for data that was never stored.
    """
    sessionmaker: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker
    async with sessionmaker() as session:
        try:
            yield session
        except Exception:
            # Re-raised for the exception handlers to report. Cancellation skips this branch;
            # closing the session on exit from the `async with` rolls back in that case.
            await session.rollback()
            raise
        await session.commit()


DbSessionDep = Annotated[AsyncSession, Depends(get_db_session, scope="function")]


def get_rate_limiter(request: Request) -> RateLimiter:
    state = request.app.state
    redis: Redis = state.redis
    return RateLimiter(redis, state.settings)


RateLimiterDep = Annotated[RateLimiter, Depends(get_rate_limiter)]


def get_auth_service(session: DbSessionDep, settings: SettingsDep) -> AuthService:
    return AuthService(session, settings)


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]


class UnauthenticatedError(AppError):
    """No usable `Authorization: Bearer <token>` header was sent."""

    status_code = HTTPStatus.UNAUTHORIZED
    code = "UNAUTHENTICATED"

    def __init__(self) -> None:
        super().__init__("Authentication required.", headers=dict(WWW_AUTHENTICATE_BEARER))


# `auto_error=False`: a missing header or a non-Bearer scheme yields None, which is reported in
# the standard envelope below instead of FastAPI's default 403. It also documents the scheme.
_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    auth_service: AuthServiceDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
) -> User:
    """The active user behind a valid access token whose session is still live.

    Missing or malformed header -> 401 `UNAUTHENTICATED`; expired token -> 401
    `TOKEN_EXPIRED`; anything else wrong -> 401 `TOKEN_INVALID`. Every 401 from here carries
    `WWW-Authenticate: Bearer`.
    """
    if credentials is None:
        raise UnauthenticatedError()
    try:
        return await auth_service.authenticate_access_token(credentials.credentials)
    except AppError as exc:
        if exc.status_code == HTTPStatus.UNAUTHORIZED:
            exc.headers = {**exc.headers, **WWW_AUTHENTICATE_BEARER}
        raise


CurrentUserDep = Annotated[User, Depends(get_current_user)]


def get_workspace_service(session: DbSessionDep) -> WorkspaceService:
    return WorkspaceService(session)


WorkspaceServiceDep = Annotated[WorkspaceService, Depends(get_workspace_service)]


def require_workspace_permission(
    permission: Permission,
) -> Callable[[uuid.UUID, User, AsyncSession], Awaitable[WorkspaceAccess]]:
    """Dependency factory resolving the `{workspace_id}` path param for the current user.

    The workspace and the caller's membership come from one query scoped to the caller, never
    a fetch-then-compare. Nonexistent workspace or not a member -> 404 `NOT_FOUND` (the two are
    indistinguishable); a member whose role lacks `permission` -> 403 `FORBIDDEN`.
    """

    async def resolve_workspace_access(
        workspace_id: uuid.UUID, current_user: CurrentUserDep, session: DbSessionDep
    ) -> WorkspaceAccess:
        access = await WorkspaceRepository(session).get_access(workspace_id, current_user.id)
        if access is None:
            raise WorkspaceNotFoundError()
        if not has_permission(access.membership.role, permission):
            raise ForbiddenError()
        return access

    return resolve_workspace_access


WorkspaceReadDep = Annotated[
    WorkspaceAccess, Depends(require_workspace_permission(Permission.WORKSPACE_READ))
]
WorkspaceManageMembersDep = Annotated[
    WorkspaceAccess, Depends(require_workspace_permission(Permission.WORKSPACE_MANAGE_MEMBERS))
]
