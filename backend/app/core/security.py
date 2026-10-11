"""Password hashing, access-token signing/verification and refresh-token primitives.

Nothing in this module logs or echoes secrets: error messages are fixed strings and never
contain token contents, claims or passwords.
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from uuid import UUID

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import Settings
from app.core.exceptions import AppError

ACCESS_TOKEN_TYPE = "access"  # noqa: S105 - JWT "typ" claim value, not a secret
REQUIRED_ACCESS_CLAIMS = ("sub", "sid", "iss", "aud", "typ", "iat", "exp")
# Tolerated clock skew for an `iat` in the future when the clock is injected.
ISSUED_AT_LEEWAY = timedelta(seconds=30)

_password_hasher = PasswordHasher()
# Precomputed once so unknown-email logins spend the same time verifying as real ones.
_DUMMY_PASSWORD_HASH = _password_hasher.hash(secrets.token_urlsafe(32))


class TokenExpiredError(AppError):
    """The access token's signature is valid but it has expired."""

    status_code = HTTPStatus.UNAUTHORIZED
    code = "TOKEN_EXPIRED"

    def __init__(self) -> None:
        super().__init__("Access token has expired.")


class TokenInvalidError(AppError):
    """The access token is malformed, forged, or has wrong or missing claims."""

    status_code = HTTPStatus.UNAUTHORIZED
    code = "TOKEN_INVALID"

    def __init__(self) -> None:
        super().__init__("Access token is invalid.")


@dataclass(frozen=True, slots=True)
class AccessTokenClaims:
    """Verified identity carried by an access token."""

    user_id: UUID
    session_id: UUID
    expires_at: datetime


def hash_password(password: str) -> str:
    """Hash a password with argon2id (library defaults; salt is generated per hash)."""
    return _password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """Return True only if `password` matches `password_hash`; never raises on bad input."""
    try:
        return _password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    """True when the hash was produced with parameters weaker than the current defaults."""
    return _password_hasher.check_needs_rehash(password_hash)


def verify_dummy_password(password: str) -> None:
    """Spend the cost of one verification so unknown accounts are not revealed by timing."""
    verify_password(_DUMMY_PASSWORD_HASH, password)


def create_access_token(
    settings: Settings, user_id: UUID, session_id: UUID, now: datetime | None = None
) -> tuple[str, int]:
    """Sign a short-lived access token; returns `(token, expires_in_seconds)`."""
    issued_at = now or datetime.now(UTC)
    expires_in = settings.access_token_ttl_minutes * 60
    payload = {
        "sub": str(user_id),
        "sid": str(session_id),
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "typ": ACCESS_TOKEN_TYPE,
        "iat": int(issued_at.timestamp()),
        "exp": int((issued_at + timedelta(seconds=expires_in)).timestamp()),
    }
    token = jwt.encode(
        payload, settings.jwt_secret_key.get_secret_value(), algorithm=settings.jwt_algorithm
    )
    return token, expires_in


def decode_access_token(
    settings: Settings, token: str, now: datetime | None = None
) -> AccessTokenClaims:
    """Verify signature, pinned algorithm, issuer, audience, expiry and type of an access token.

    `now` overrides the clock: PyJWT has no clock injection, so its own `exp`/`iat` checks
    are disabled and both are verified here against `now` instead (`iat` may be at most
    `ISSUED_AT_LEEWAY` in the future). With `now=None` PyJWT checks both against the real clock.
    """
    options: dict[str, object] = {"require": list(REQUIRED_ACCESS_CLAIMS)}
    if now is not None:
        options["verify_exp"] = False
        options["verify_iat"] = False
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options=options,  # type: ignore[arg-type]
        )
    except jwt.ExpiredSignatureError:
        raise TokenExpiredError() from None
    except jwt.PyJWTError:
        raise TokenInvalidError() from None

    if payload.get("typ") != ACCESS_TOKEN_TYPE:
        raise TokenInvalidError()
    try:
        user_id = UUID(str(payload["sub"]))
        session_id = UUID(str(payload["sid"]))
        expires_at = datetime.fromtimestamp(int(payload["exp"]), tz=UTC)
        issued_at = datetime.fromtimestamp(int(payload["iat"]), tz=UTC)
    except (TypeError, ValueError, OverflowError, OSError):
        raise TokenInvalidError() from None
    if now is not None:
        if issued_at > now + ISSUED_AT_LEEWAY:
            raise TokenInvalidError()
        if expires_at <= now:
            raise TokenExpiredError()
    return AccessTokenClaims(user_id=user_id, session_id=session_id, expires_at=expires_at)


def new_refresh_token() -> str:
    """Generate an opaque, URL-safe refresh token (256 bits of entropy)."""
    return secrets.token_urlsafe(32)


def hash_refresh_token(raw_token: str) -> str:
    """SHA-256 hex digest of a refresh token; only this value is stored."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
