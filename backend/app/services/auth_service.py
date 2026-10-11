"""Registration, login, refresh-token rotation, logout and access-token authentication.

Business rules live here; queries live in the repositories. The caller (the request's unit of
work) commits on success. Paths that must persist despite failing - failed-login counters and
audits, blocked attempts, refresh-token reuse - commit explicitly *before* raising, as described
on `app.core.dependencies.get_db_session`.

Nothing here logs or audits emails, IP addresses, passwords, tokens or throttle key hashes.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from http import HTTPStatus

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import AppError
from app.core.security import (
    TokenExpiredError,
    TokenInvalidError,
    create_access_token,
    decode_access_token,
    hash_password,
    hash_refresh_token,
    new_refresh_token,
    password_needs_rehash,
    verify_dummy_password,
    verify_password,
)
from app.models import RefreshToken, User
from app.repositories.refresh_token_repository import RefreshTokenRepository
from app.repositories.user_repository import MembershipSummary, UserRepository
from app.services.audit_service import AuditAction, AuditService
from app.services.login_throttle import LoginThrottleService, throttle_key

PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 128
FULL_NAME_MAX_LENGTH = 200


class InvalidCredentialsError(AppError):
    """Wrong password, unknown email or inactive account - deliberately indistinguishable."""

    status_code = HTTPStatus.UNAUTHORIZED
    code = "INVALID_CREDENTIALS"

    def __init__(self) -> None:
        super().__init__("Invalid email or password.")


class LoginThrottledError(AppError):
    """The (email, IP) pair is blocked after repeated failures."""

    status_code = HTTPStatus.TOO_MANY_REQUESTS
    code = "LOGIN_THROTTLED"

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(
            "Too many sign-in attempts. Please wait and try again.",
            headers={"Retry-After": str(retry_after_seconds)},
        )
        self.retry_after_seconds = retry_after_seconds


class EmailTakenError(AppError):
    status_code = HTTPStatus.CONFLICT
    code = "EMAIL_TAKEN"

    def __init__(self) -> None:
        super().__init__("An account with this email already exists.")


class TokenReusedError(AppError):
    """A rotated (revoked) refresh token was presented again; every session was revoked."""

    status_code = HTTPStatus.UNAUTHORIZED
    code = "TOKEN_REUSED"

    def __init__(self) -> None:
        super().__init__("This session is no longer valid. Please sign in again.")


class RefreshTokenInvalidError(TokenInvalidError):
    """Unknown refresh token, one revoked without being rotated (logout, reuse response), or
    one whose user no longer exists or is inactive."""

    def __init__(self) -> None:
        AppError.__init__(self, "Refresh token is invalid.")


class RefreshTokenExpiredError(TokenExpiredError):
    def __init__(self) -> None:
        AppError.__init__(self, "Refresh token has expired.")


@dataclass(frozen=True, slots=True)
class LoginResult:
    access_token: str
    expires_in: int
    refresh_token_raw: str
    refresh_expires_at: datetime
    user: User


@dataclass(frozen=True, slots=True)
class RefreshResult:
    access_token: str
    expires_in: int
    refresh_token_raw: str
    refresh_expires_at: datetime


@dataclass(frozen=True, slots=True)
class MeResult:
    user: User
    memberships: list[MembershipSummary]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def normalize_email(email: str) -> str:
    return email.strip().lower()


def _validation_error(field: str, message: str, error_type: str) -> AppError:
    return AppError(
        "Request validation failed.",
        code="VALIDATION_ERROR",
        status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
        details=[{"field": field, "message": message, "type": error_type}],
    )


def validate_password(password: str, normalized_email: str) -> None:
    """Enforce the password policy: 12-128 characters and not the account's email."""
    if len(password) < PASSWORD_MIN_LENGTH:
        raise _validation_error(
            "password",
            f"Password must be at least {PASSWORD_MIN_LENGTH} characters.",
            "string_too_short",
        )
    if len(password) > PASSWORD_MAX_LENGTH:
        raise _validation_error(
            "password",
            f"Password must be at most {PASSWORD_MAX_LENGTH} characters.",
            "string_too_long",
        )
    if password.strip().lower() == normalized_email:
        raise _validation_error(
            "password", "Password must not be the same as the email.", "password_equals_email"
        )


class AuthService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._session = session
        self._settings = settings
        self._now = now
        self._users = UserRepository(session)
        self._tokens = RefreshTokenRepository(session)
        self._throttle = LoginThrottleService(session, settings)
        self._audit = AuditService(session)

    async def register(self, email: str, password: str, full_name: str) -> User:
        """Create an account. Does not sign in. Raises `EmailTakenError` on a duplicate."""
        normalized_email = normalize_email(email)
        name = full_name.strip()
        if not name:
            raise _validation_error("full_name", "Full name must not be empty.", "string_too_short")
        if len(name) > FULL_NAME_MAX_LENGTH:
            raise _validation_error(
                "full_name",
                f"Full name must be at most {FULL_NAME_MAX_LENGTH} characters.",
                "string_too_long",
            )
        validate_password(password, normalized_email)

        if await self._users.get_by_email(normalized_email) is not None:
            raise EmailTakenError()
        user = User(email=normalized_email, password_hash=hash_password(password), full_name=name)
        try:
            await self._users.add(user)
        except IntegrityError as exc:
            # A concurrent registration won the race for the same email.
            if "uq_users_email" in str(exc.orig):
                raise EmailTakenError() from exc
            raise
        await self._audit.record(
            AuditAction.AUTH_REGISTERED, entity_type="user", entity_id=user.id, actor_id=user.id
        )
        return user

    async def login(self, email: str, password: str, client_ip: str) -> LoginResult:
        """Verify credentials under the progressive (email, IP) throttle and open a session."""
        now = self._now()
        normalized_email = normalize_email(email)
        key_hash = throttle_key(normalized_email, client_ip)

        retry_after = await self._throttle.retry_after_seconds(key_hash, now)
        if retry_after is not None:
            # The password is deliberately not checked while the pair is blocked.
            blocked_user = await self._users.get_by_email(normalized_email)
            blocked_user_id = blocked_user.id if blocked_user is not None else None
            await self._audit.record(
                AuditAction.AUTH_LOGIN_BLOCKED_ATTEMPT,
                entity_type="user",
                entity_id=blocked_user_id,
                actor_id=blocked_user_id,
                metadata={"retry_after_seconds": retry_after},
            )
            await self._session.commit()
            raise LoginThrottledError(retry_after)

        user = await self._users.get_by_email(normalized_email)
        failure_reason: str | None = None
        if user is None:
            verify_dummy_password(password)
            failure_reason = "unknown_user"
        elif not verify_password(user.password_hash, password):
            failure_reason = "bad_password"
        elif not user.is_active:
            failure_reason = "inactive"

        if failure_reason is not None:
            await self._record_failed_login(key_hash, now, user, failure_reason)
            raise InvalidCredentialsError()
        assert user is not None  # noqa: S101 - narrowed: every user-is-None path failed above

        await self._throttle.clear(key_hash)
        user.last_login_at = now
        if password_needs_rehash(user.password_hash):
            user.password_hash = hash_password(password)
        family_id = uuid.uuid4()
        refresh_token_raw, issued = await self._issue_refresh_token(
            user.id,
            family_id,
            expires_at=now + timedelta(days=self._settings.refresh_token_ttl_days),
        )
        access_token, expires_in = create_access_token(self._settings, user.id, family_id, now=now)
        await self._audit.record(
            AuditAction.AUTH_LOGIN_SUCCEEDED,
            entity_type="user",
            entity_id=user.id,
            actor_id=user.id,
        )
        return LoginResult(
            access_token=access_token,
            expires_in=expires_in,
            refresh_token_raw=refresh_token_raw,
            refresh_expires_at=issued.expires_at,
            user=user,
        )

    async def _record_failed_login(
        self, key_hash: str, now: datetime, user: User | None, reason: str
    ) -> None:
        """Count the failure, audit it, and commit so both survive the 401."""
        outcome = await self._throttle.record_failure(key_hash, now)
        user_id = user.id if user is not None else None
        await self._audit.record(
            AuditAction.AUTH_LOGIN_FAILED,
            entity_type="user",
            entity_id=user_id,
            actor_id=user_id,
            metadata={"reason": reason, "failure_count": outcome.failure_count},
        )
        if outcome.delay_seconds > 0:
            await self._audit.record(
                AuditAction.AUTH_LOGIN_THROTTLED,
                entity_type="user",
                entity_id=user_id,
                actor_id=user_id,
                metadata={
                    "failure_count": outcome.failure_count,
                    "delay_seconds": outcome.delay_seconds,
                },
            )
        await self._session.commit()

    async def refresh(self, raw_refresh_token: str) -> RefreshResult:
        """Rotate a refresh token. Presenting an already-rotated one revokes every session.

        Only a token that was rotated (`replaced_by_id` set) counts as reuse: one revoked by
        logout or by an earlier reuse response is merely invalid and revokes nothing else, so
        replaying a signed-out cookie cannot sign the user out everywhere. The successor keeps
        the presented token's `expires_at`, so a session lives at most
        `refresh_token_ttl_days` from login however often it is refreshed.
        """
        now = self._now()
        token = await self._tokens.get_by_hash_for_update(hash_refresh_token(raw_refresh_token))
        if token is None:
            raise RefreshTokenInvalidError()
        if token.revoked_at is not None and token.replaced_by_id is None:
            raise RefreshTokenInvalidError()
        if token.revoked_at is not None:
            await self._tokens.revoke_all_for_user(token.user_id, now)
            await self._audit.record(
                AuditAction.AUTH_REFRESH_REUSED,
                entity_type="refresh_token",
                entity_id=token.id,
                actor_id=token.user_id,
                metadata={"family_id": str(token.family_id)},
            )
            await self._session.commit()
            raise TokenReusedError()
        if token.expires_at <= now:
            raise RefreshTokenExpiredError()
        user = await self._users.get_by_id(token.user_id)
        if user is None or not user.is_active:
            raise RefreshTokenInvalidError()

        refresh_token_raw, successor = await self._issue_refresh_token(
            token.user_id, token.family_id, expires_at=token.expires_at
        )
        token.revoked_at = now
        token.replaced_by_id = successor.id
        await self._session.flush()
        access_token, expires_in = create_access_token(
            self._settings, token.user_id, token.family_id, now=now
        )
        return RefreshResult(
            access_token=access_token,
            expires_in=expires_in,
            refresh_token_raw=refresh_token_raw,
            refresh_expires_at=successor.expires_at,
        )

    async def logout(self, raw_refresh_token: str | None) -> None:
        """Revoke the presented token's whole session. Unknown, missing or already-ended: no-op.

        The presented row is locked first - the same lock `refresh` takes - so a concurrent
        rotation either finishes before this (and the family revocation below, a separate
        statement with a fresh snapshot, sees and revokes its successor) or starts after it
        and finds the token revoked.
        """
        if not raw_refresh_token:
            return
        token = await self._tokens.get_by_hash_for_update(hash_refresh_token(raw_refresh_token))
        if token is None:
            return
        # A session that already ended is a no-op, so replaying a stale cookie cannot flood the
        # audit log with logout events attributed to its user.
        if await self._tokens.revoke_family(token.family_id, self._now()) == 0:
            return
        await self._audit.record(
            AuditAction.AUTH_LOGGED_OUT,
            entity_type="user",
            entity_id=token.user_id,
            actor_id=token.user_id,
        )

    async def authenticate_access_token(self, token: str) -> User:
        """Resolve an access token to its active user whose session is still live.

        Raises `TokenExpiredError` for an expired token, `TokenInvalidError` otherwise.
        """
        now = self._now()
        claims = decode_access_token(self._settings, token, now=now)
        user = await self._users.get_by_id(claims.user_id)
        if user is None or not user.is_active:
            raise TokenInvalidError()
        if not await self._tokens.family_is_live(claims.session_id, user.id, now):
            raise TokenInvalidError()
        return user

    async def get_me(self, user: User) -> MeResult:
        return MeResult(user=user, memberships=await self._users.list_memberships(user.id))

    async def _issue_refresh_token(
        self, user_id: uuid.UUID, family_id: uuid.UUID, *, expires_at: datetime
    ) -> tuple[str, RefreshToken]:
        """Store a new refresh token in session `family_id`; returns `(raw, row)`."""
        raw = new_refresh_token()
        row = await self._tokens.add(
            RefreshToken(
                user_id=user_id,
                family_id=family_id,
                token_hash=hash_refresh_token(raw),
                expires_at=expires_at,
            )
        )
        return raw, row
