"""`AuthService` against a real PostgreSQL: registration, throttled login, rotation, logout.

Time is driven by an injected mutable clock, so throttle windows, the 24 h reset and token
expiry are exercised without sleeping. Paths that must persist despite raising are checked
after a `rollback()`, which proves the service committed them itself.
"""

import asyncio
from collections import Counter
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from argon2 import PasswordHasher
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import Settings
from app.core.exceptions import AppError
from app.core.security import (
    TokenExpiredError,
    TokenInvalidError,
    hash_refresh_token,
    password_needs_rehash,
    verify_password,
)
from app.db.session import build_sessionmaker
from app.models import (
    AuditLog,
    LoginThrottle,
    RefreshToken,
    User,
    UserRole,
    Workspace,
    WorkspaceMember,
)
from app.services.auth_service import (
    AuthService,
    EmailTakenError,
    InvalidCredentialsError,
    LoginThrottledError,
    RefreshTokenInvalidError,
    TokenReusedError,
)
from app.services.login_throttle import throttle_key
from tests.conftest import make_settings

pytestmark = pytest.mark.integration

SECRET = "test-secret-key-that-is-long-enough-for-hs256-0123456789"
EMAIL = "Ada.Lovelace@Example.com"
NORMALIZED_EMAIL = "ada.lovelace@example.com"
PASSWORD = "correct horse battery"
WRONG_PASSWORD = "wrong horse battery"
UNKNOWN_EMAIL = "nobody.here@example.com"
IP_A = "203.0.113.7"
IP_B = "198.51.100.9"
# A fixed instant keeps throttle windows and token expiry deterministic; `decode_access_token`
# checks `iat` and `exp` against the injected clock, so any date works.
START = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)


class MutableClock:
    """A wall clock that tests move forward explicitly."""

    def __init__(self, start: datetime) -> None:
        self.current = start

    def __call__(self) -> datetime:
        return self.current

    def advance(self, delta: timedelta) -> None:
        self.current += delta


@pytest.fixture
def auth_settings(migrated_database_url: str, truncate_tables: None) -> Settings:
    return make_settings(
        database_url=migrated_database_url,
        jwt_secret_key=SECRET,
        access_token_ttl_minutes=15,
        refresh_token_ttl_days=7,
        login_free_attempts=4,
        login_base_delay_seconds=30,
        login_max_delay_seconds=900,
        login_throttle_reset_hours=24,
        rate_limit_enabled=False,
    )


@pytest.fixture
def clock() -> MutableClock:
    return MutableClock(START)


@pytest.fixture
def service(db_session: AsyncSession, auth_settings: Settings, clock: MutableClock) -> AuthService:
    return AuthService(db_session, auth_settings, now=clock)


@pytest.fixture
async def session_factory(
    auth_settings: Settings,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Independent sessions (one connection each) for running service calls concurrently."""
    engine = create_async_engine(auth_settings.database_url, poolclass=NullPool)
    try:
        yield build_sessionmaker(engine)
    finally:
        await engine.dispose()


@pytest.fixture
async def user(service: AuthService, db_session: AsyncSession) -> User:
    registered = await service.register(EMAIL, PASSWORD, "  Ada Lovelace  ")
    await db_session.commit()
    return registered


def _error_view(error: AppError) -> tuple[object, ...]:
    return (error.status_code, error.code, error.message, error.details, error.headers)


async def _failed_login(service: AuthService, email: str, password: str, ip: str) -> AppError:
    with pytest.raises(AppError) as caught:
        await service.login(email, password, ip)
    return caught.value


async def _fail_times(service: AuthService, times: int, email: str = EMAIL, ip: str = IP_A) -> None:
    for _ in range(times):
        with pytest.raises(InvalidCredentialsError):
            await service.login(email, WRONG_PASSWORD, ip)


async def _discard_uncommitted(session: AsyncSession, user: User | None = None) -> None:
    """Roll back whatever the service did not commit itself, then reload `user`.

    Rollback expires every loaded instance, and async sessions cannot lazy-load, so `user`
    is refreshed explicitly.
    """
    await session.rollback()
    if user is not None:
        await session.refresh(user)


async def _audit_rows(session: AsyncSession, action: str) -> list[AuditLog]:
    result = await session.execute(
        select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.created_at)
    )
    return list(result.scalars())


async def _throttle_row(session: AsyncSession, email: str, ip: str) -> LoginThrottle | None:
    result = await session.execute(
        select(LoginThrottle)
        .where(LoginThrottle.key_hash == throttle_key(email, ip))
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def _tokens_of(session: AsyncSession, user: User) -> list[RefreshToken]:
    result = await session.execute(
        select(RefreshToken)
        .where(RefreshToken.user_id == user.id)
        .execution_options(populate_existing=True)
    )
    return list(result.scalars())


# --- register -------------------------------------------------------------------------------


async def test_register_normalizes_input_hashes_the_password_and_audits(
    service: AuthService, db_session: AsyncSession, user: User
) -> None:
    assert user.email == NORMALIZED_EMAIL
    assert user.full_name == "Ada Lovelace"
    assert user.password_hash != PASSWORD
    assert verify_password(user.password_hash, PASSWORD)
    assert user.is_active is True
    [audit] = await _audit_rows(db_session, "AUTH_REGISTERED")
    assert audit.actor_id == user.id
    assert audit.entity_id == user.id


async def test_register_rejects_a_duplicate_email_in_any_case(
    service: AuthService, db_session: AsyncSession, user: User
) -> None:
    with pytest.raises(EmailTakenError) as caught:
        await service.register("  ADA.LOVELACE@example.COM ", PASSWORD, "Someone Else")

    assert caught.value.status_code == 409
    assert caught.value.code == "EMAIL_TAKEN"
    assert await db_session.scalar(select(func.count(User.id))) == 1


@pytest.mark.parametrize(
    ("password", "error_type"),
    [
        ("a" * 11, "string_too_short"),
        ("a" * 129, "string_too_long"),
        ("person@example.com", "password_equals_email"),
        ("PERSON@EXAMPLE.COM", "password_equals_email"),
    ],
)
async def test_register_enforces_the_password_policy(
    service: AuthService, db_session: AsyncSession, password: str, error_type: str
) -> None:
    with pytest.raises(AppError) as caught:
        await service.register("person@example.com", password, "Person")

    assert caught.value.status_code == 422
    assert caught.value.code == "VALIDATION_ERROR"
    assert [(d["field"], d["type"]) for d in caught.value.details] == [("password", error_type)]
    assert await db_session.scalar(select(func.count(User.id))) == 0


@pytest.mark.parametrize("length", [12, 128])
async def test_register_accepts_passwords_at_the_length_limits(
    service: AuthService, length: int
) -> None:
    registered = await service.register("person@example.com", "p" * length, "Person")

    assert registered.id is not None


async def test_register_rejects_a_blank_full_name(service: AuthService) -> None:
    with pytest.raises(AppError) as caught:
        await service.register("person@example.com", PASSWORD, "   ")

    assert caught.value.code == "VALIDATION_ERROR"
    assert caught.value.details[0]["field"] == "full_name"


# --- login ----------------------------------------------------------------------------------


async def test_login_opens_a_session_and_returns_a_working_access_token(
    service: AuthService, db_session: AsyncSession, user: User
) -> None:
    result = await service.login(EMAIL, PASSWORD, IP_A)
    await db_session.commit()

    assert result.user.id == user.id
    assert result.expires_in == 15 * 60
    assert (await service.authenticate_access_token(result.access_token)).id == user.id
    [token] = await _tokens_of(db_session, user)
    assert token.token_hash == hash_refresh_token(result.refresh_token_raw)
    assert token.expires_at == START + timedelta(days=7)
    assert token.revoked_at is None
    assert user.last_login_at == START
    [audit] = await _audit_rows(db_session, "AUTH_LOGIN_SUCCEEDED")
    assert audit.actor_id == user.id


async def test_each_login_opens_a_new_session_family(
    service: AuthService, db_session: AsyncSession, user: User
) -> None:
    await service.login(EMAIL, PASSWORD, IP_A)
    await service.login(EMAIL, PASSWORD, IP_A)
    await db_session.commit()

    assert len({token.family_id for token in await _tokens_of(db_session, user)}) == 2


async def test_login_rehashes_a_password_hashed_with_weaker_parameters(
    service: AuthService, db_session: AsyncSession, user: User
) -> None:
    weak_hash = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash(PASSWORD)
    user.password_hash = weak_hash
    await db_session.commit()

    await service.login(EMAIL, PASSWORD, IP_A)
    await db_session.commit()

    assert user.password_hash != weak_hash
    assert not password_needs_rehash(user.password_hash)
    assert verify_password(user.password_hash, PASSWORD)


async def test_failed_login_is_counted_and_audited_even_though_it_raises(
    service: AuthService, db_session: AsyncSession, user: User
) -> None:
    error = await _failed_login(service, EMAIL, WRONG_PASSWORD, IP_A)
    await _discard_uncommitted(db_session, user)

    assert isinstance(error, InvalidCredentialsError)
    assert (error.status_code, error.code, error.message) == (
        401,
        "INVALID_CREDENTIALS",
        "Invalid email or password.",
    )
    row = await _throttle_row(db_session, EMAIL, IP_A)
    assert row is not None
    assert row.failure_count == 1
    assert row.blocked_until is None
    [audit] = await _audit_rows(db_session, "AUTH_LOGIN_FAILED")
    assert audit.actor_id == user.id
    assert audit.action_metadata == {"reason": "bad_password", "failure_count": 1}


async def test_failed_login_for_an_unknown_email_is_audited_without_an_actor(
    service: AuthService, db_session: AsyncSession
) -> None:
    await _fail_times(service, 1, email=UNKNOWN_EMAIL)
    await _discard_uncommitted(db_session)

    [audit] = await _audit_rows(db_session, "AUTH_LOGIN_FAILED")
    assert audit.actor_id is None
    assert audit.entity_id is None
    assert audit.action_metadata == {"reason": "unknown_user", "failure_count": 1}


async def test_inactive_user_cannot_log_in_with_the_correct_password(
    service: AuthService, db_session: AsyncSession, user: User
) -> None:
    user.is_active = False
    await db_session.commit()

    error = await _failed_login(service, EMAIL, PASSWORD, IP_A)

    assert isinstance(error, InvalidCredentialsError)
    [audit] = await _audit_rows(db_session, "AUTH_LOGIN_FAILED")
    assert audit.action_metadata == {"reason": "inactive", "failure_count": 1}
    assert await _tokens_of(db_session, user) == []


# --- progressive throttling -----------------------------------------------------------------


async def test_fourth_failure_blocks_the_pair_and_audits_the_throttle(
    service: AuthService, db_session: AsyncSession, user: User
) -> None:
    await _fail_times(service, 3)
    assert await _audit_rows(db_session, "AUTH_LOGIN_THROTTLED") == []

    await _fail_times(service, 1)
    await _discard_uncommitted(db_session, user)

    row = await _throttle_row(db_session, EMAIL, IP_A)
    assert row is not None
    assert row.failure_count == 4
    assert row.blocked_until == START + timedelta(seconds=30)
    [audit] = await _audit_rows(db_session, "AUTH_LOGIN_THROTTLED")
    assert audit.actor_id == user.id
    assert audit.action_metadata == {"failure_count": 4, "delay_seconds": 30}


async def test_attempt_while_blocked_is_throttled_even_with_the_correct_password(
    service: AuthService, db_session: AsyncSession, user: User, clock: MutableClock
) -> None:
    await _fail_times(service, 4)
    clock.advance(timedelta(seconds=10))

    error = await _failed_login(service, EMAIL, PASSWORD, IP_A)
    await _discard_uncommitted(db_session, user)

    assert isinstance(error, LoginThrottledError)
    assert (error.status_code, error.code, error.message) == (
        429,
        "LOGIN_THROTTLED",
        "Too many sign-in attempts. Please wait and try again.",
    )
    assert error.headers == {"Retry-After": "20"}
    [audit] = await _audit_rows(db_session, "AUTH_LOGIN_BLOCKED_ATTEMPT")
    assert audit.action_metadata == {"retry_after_seconds": 20}
    row = await _throttle_row(db_session, EMAIL, IP_A)
    assert row is not None
    assert row.failure_count == 4  # a blocked attempt is not counted as a failure
    assert await _tokens_of(db_session, user) == []


async def test_correct_password_after_the_window_succeeds_and_deletes_the_throttle_row(
    service: AuthService, db_session: AsyncSession, user: User, clock: MutableClock
) -> None:
    await _fail_times(service, 4)
    clock.advance(timedelta(seconds=30))

    result = await service.login(EMAIL, PASSWORD, IP_A)
    await db_session.commit()

    assert result.user.id == user.id
    assert await _throttle_row(db_session, EMAIL, IP_A) is None


async def test_failure_after_a_block_expires_doubles_the_delay(
    service: AuthService, db_session: AsyncSession, user: User, clock: MutableClock
) -> None:
    await _fail_times(service, 4)
    clock.advance(timedelta(seconds=31))

    await _fail_times(service, 1)
    error = await _failed_login(service, EMAIL, PASSWORD, IP_A)

    assert isinstance(error, LoginThrottledError)
    assert error.headers == {"Retry-After": "60"}


async def test_failures_from_one_ip_do_not_throttle_the_same_email_from_another(
    service: AuthService, db_session: AsyncSession, user: User
) -> None:
    await _fail_times(service, 4, ip=IP_A)

    result = await service.login(EMAIL, PASSWORD, IP_B)

    assert result.user.id == user.id
    assert isinstance(await _failed_login(service, EMAIL, PASSWORD, IP_A), LoginThrottledError)


async def test_unknown_email_gets_responses_identical_to_a_known_one(
    service: AuthService, db_session: AsyncSession, user: User, clock: MutableClock
) -> None:
    async def attempt_sequence(email: str) -> list[tuple[object, ...]]:
        views = [
            _error_view(await _failed_login(service, email, WRONG_PASSWORD, IP_A)) for _ in range(4)
        ]
        views.append(_error_view(await _failed_login(service, email, WRONG_PASSWORD, IP_A)))
        return views

    known = await attempt_sequence(EMAIL)
    unknown = await attempt_sequence(UNKNOWN_EMAIL)

    assert known == unknown
    assert [view[1] for view in known] == ["INVALID_CREDENTIALS"] * 4 + ["LOGIN_THROTTLED"]
    assert known[-1][4] == {"Retry-After": "30"}


async def test_failure_counts_older_than_the_reset_window_start_over(
    service: AuthService, db_session: AsyncSession, user: User, clock: MutableClock
) -> None:
    await _fail_times(service, 3)
    clock.advance(timedelta(hours=24, seconds=1))

    await _fail_times(service, 1)
    await _discard_uncommitted(db_session, user)

    row = await _throttle_row(db_session, EMAIL, IP_A)
    assert row is not None
    assert row.failure_count == 1
    assert row.blocked_until is None
    assert (await service.login(EMAIL, PASSWORD, IP_A)).user.id == user.id


async def test_throttle_rows_and_audit_metadata_never_contain_the_email_or_ip(
    service: AuthService, db_session: AsyncSession, user: User
) -> None:
    await _fail_times(service, 4, email=EMAIL)
    await _fail_times(service, 4, email=UNKNOWN_EMAIL)
    for email in (EMAIL, UNKNOWN_EMAIL):
        assert isinstance(await _failed_login(service, email, PASSWORD, IP_A), LoginThrottledError)
    await _discard_uncommitted(db_session, user)

    throttle_text = await db_session.scalar(
        text(
            "SELECT string_agg(concat_ws('|', key_hash, failure_count, last_failure_at, "
            "blocked_until, updated_at), ';') FROM login_throttles"
        )
    )
    metadata_text = await db_session.scalar(
        text("SELECT string_agg(action_metadata::text, ';') FROM audit_logs")
    )
    assert throttle_text and metadata_text
    for stored in (throttle_text.lower(), metadata_text.lower()):
        for secret in (NORMALIZED_EMAIL, UNKNOWN_EMAIL, IP_A, PASSWORD, WRONG_PASSWORD):
            assert secret not in stored


async def _login_in_own_session(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    clock: MutableClock,
    password: str,
) -> str:
    """One login attempt in its own session and transaction, as a separate request would be."""
    async with factory() as session:
        try:
            await AuthService(session, settings, now=clock).login(EMAIL, password, IP_A)
        except AppError as error:
            await session.rollback()
            return error.code
        await session.commit()
        return "OK"


async def test_a_concurrent_burst_on_a_fresh_pair_gets_only_the_free_password_checks(
    session_factory: async_sessionmaker[AsyncSession],
    auth_settings: Settings,
    db_session: AsyncSession,
    user: User,
    clock: MutableClock,
) -> None:
    outcomes = await asyncio.gather(
        *(
            _login_in_own_session(session_factory, auth_settings, clock, WRONG_PASSWORD)
            for _ in range(10)
        )
    )

    assert Counter(outcomes) == {"INVALID_CREDENTIALS": 4, "LOGIN_THROTTLED": 6}
    row = await _throttle_row(db_session, EMAIL, IP_A)
    assert row is not None
    assert row.failure_count == 4
    assert len(await _audit_rows(db_session, "AUTH_LOGIN_FAILED")) == 4


# --- refresh --------------------------------------------------------------------------------


async def test_refresh_rotates_the_token_within_the_same_session(
    service: AuthService, db_session: AsyncSession, user: User
) -> None:
    login = await service.login(EMAIL, PASSWORD, IP_A)
    await db_session.commit()

    rotated = await service.refresh(login.refresh_token_raw)
    await db_session.commit()

    assert rotated.refresh_token_raw != login.refresh_token_raw
    tokens = {token.token_hash: token for token in await _tokens_of(db_session, user)}
    old = tokens[hash_refresh_token(login.refresh_token_raw)]
    new = tokens[hash_refresh_token(rotated.refresh_token_raw)]
    assert old.revoked_at == START
    assert old.replaced_by_id == new.id
    assert new.family_id == old.family_id
    assert new.revoked_at is None
    assert (await service.authenticate_access_token(rotated.access_token)).id == user.id
    again = await service.refresh(rotated.refresh_token_raw)
    assert again.refresh_token_raw not in {login.refresh_token_raw, rotated.refresh_token_raw}


async def test_reusing_a_rotated_token_revokes_every_session_of_the_user(
    service: AuthService, db_session: AsyncSession, user: User
) -> None:
    first = await service.login(EMAIL, PASSWORD, IP_A)
    second = await service.login(EMAIL, PASSWORD, IP_B)
    await db_session.commit()
    rotated = await service.refresh(first.refresh_token_raw)
    await db_session.commit()

    with pytest.raises(TokenReusedError) as caught:
        await service.refresh(first.refresh_token_raw)
    await _discard_uncommitted(db_session, user)

    assert (caught.value.status_code, caught.value.code) == (401, "TOKEN_REUSED")
    assert all(token.revoked_at is not None for token in await _tokens_of(db_session, user))
    [audit] = await _audit_rows(db_session, "AUTH_REFRESH_REUSED")
    assert audit.actor_id == user.id
    for access_token in (first.access_token, rotated.access_token, second.access_token):
        with pytest.raises(TokenInvalidError):
            await service.authenticate_access_token(access_token)
    # The successor was revoked by the reuse response, not rotated, so it is merely invalid.
    with pytest.raises(RefreshTokenInvalidError):
        await service.refresh(rotated.refresh_token_raw)


async def test_refresh_rejects_an_unknown_token(service: AuthService, user: User) -> None:
    with pytest.raises(TokenInvalidError) as caught:
        await service.refresh("not-a-real-refresh-token")

    assert (caught.value.status_code, caught.value.code) == (401, "TOKEN_INVALID")


async def test_refresh_rejects_an_expired_token(
    service: AuthService, db_session: AsyncSession, user: User, clock: MutableClock
) -> None:
    login = await service.login(EMAIL, PASSWORD, IP_A)
    await db_session.commit()
    clock.advance(timedelta(days=7))

    with pytest.raises(TokenExpiredError) as caught:
        await service.refresh(login.refresh_token_raw)

    assert (caught.value.status_code, caught.value.code) == (401, "TOKEN_EXPIRED")


async def test_refreshed_token_keeps_the_sessions_original_expiry(
    service: AuthService, db_session: AsyncSession, user: User, clock: MutableClock
) -> None:
    login = await service.login(EMAIL, PASSWORD, IP_A)
    await db_session.commit()
    clock.advance(timedelta(days=7, seconds=-1))

    rotated = await service.refresh(login.refresh_token_raw)
    await db_session.commit()

    tokens = {token.token_hash: token for token in await _tokens_of(db_session, user)}
    assert tokens[hash_refresh_token(rotated.refresh_token_raw)].expires_at == START + timedelta(
        days=7
    )


async def test_refresh_after_the_sessions_absolute_lifetime_is_expired(
    service: AuthService, db_session: AsyncSession, user: User, clock: MutableClock
) -> None:
    login = await service.login(EMAIL, PASSWORD, IP_A)
    await db_session.commit()
    clock.advance(timedelta(days=7, seconds=-1))
    rotated = await service.refresh(login.refresh_token_raw)
    await db_session.commit()
    clock.advance(timedelta(seconds=1))

    with pytest.raises(TokenExpiredError) as caught:
        await service.refresh(rotated.refresh_token_raw)

    assert (caught.value.status_code, caught.value.code) == (401, "TOKEN_EXPIRED")


async def test_refresh_rejects_an_inactive_user(
    service: AuthService, db_session: AsyncSession, user: User
) -> None:
    login = await service.login(EMAIL, PASSWORD, IP_A)
    user.is_active = False
    await db_session.commit()

    with pytest.raises(TokenInvalidError):
        await service.refresh(login.refresh_token_raw)


# --- logout and access tokens ---------------------------------------------------------------


async def test_logout_revokes_the_session_and_invalidates_its_access_token(
    service: AuthService, db_session: AsyncSession, user: User
) -> None:
    login = await service.login(EMAIL, PASSWORD, IP_A)
    other = await service.login(EMAIL, PASSWORD, IP_A)
    await db_session.commit()

    await service.logout(login.refresh_token_raw)
    await db_session.commit()

    with pytest.raises(TokenInvalidError):
        await service.authenticate_access_token(login.access_token)
    assert (await service.authenticate_access_token(other.access_token)).id == user.id
    [audit] = await _audit_rows(db_session, "AUTH_LOGGED_OUT")
    assert audit.actor_id == user.id


@pytest.mark.parametrize("raw_token", [None, "", "unknown-refresh-token"])
async def test_logout_without_a_known_token_is_a_no_op(
    service: AuthService, db_session: AsyncSession, user: User, raw_token: str | None
) -> None:
    login = await service.login(EMAIL, PASSWORD, IP_A)
    await db_session.commit()

    await service.logout(raw_token)
    await db_session.commit()

    assert (await service.authenticate_access_token(login.access_token)).id == user.id
    assert await _audit_rows(db_session, "AUTH_LOGGED_OUT") == []


async def test_replaying_a_logged_out_cookie_writes_no_further_audit_rows(
    service: AuthService, db_session: AsyncSession, user: User
) -> None:
    login = await service.login(EMAIL, PASSWORD, IP_A)
    await service.logout(login.refresh_token_raw)
    await db_session.commit()

    for _ in range(3):
        await service.logout(login.refresh_token_raw)
    await db_session.commit()

    assert len(await _audit_rows(db_session, "AUTH_LOGGED_OUT")) == 1


async def test_a_logged_out_token_is_invalid_and_does_not_sign_out_other_sessions(
    service: AuthService, db_session: AsyncSession, user: User
) -> None:
    signed_out = await service.login(EMAIL, PASSWORD, IP_A)
    other = await service.login(EMAIL, PASSWORD, IP_B)
    await db_session.commit()
    await service.logout(signed_out.refresh_token_raw)
    await db_session.commit()

    with pytest.raises(RefreshTokenInvalidError) as caught:
        await service.refresh(signed_out.refresh_token_raw)
    await _discard_uncommitted(db_session, user)

    assert (caught.value.status_code, caught.value.code) == (401, "TOKEN_INVALID")
    assert await _audit_rows(db_session, "AUTH_REFRESH_REUSED") == []
    assert (await service.authenticate_access_token(other.access_token)).id == user.id
    assert (await service.refresh(other.refresh_token_raw)).refresh_token_raw


async def _refresh_in_own_session(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    clock: MutableClock,
    raw_token: str,
) -> str:
    async with factory() as session:
        try:
            await AuthService(session, settings, now=clock).refresh(raw_token)
        except AppError as error:
            await session.rollback()
            return error.code
        await session.commit()
        return "OK"


async def _logout_in_own_session(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    clock: MutableClock,
    raw_token: str,
) -> str:
    async with factory() as session:
        await AuthService(session, settings, now=clock).logout(raw_token)
        await session.commit()
        return "OK"


async def test_logout_racing_a_refresh_of_the_same_token_leaves_no_live_token(
    session_factory: async_sessionmaker[AsyncSession],
    auth_settings: Settings,
    db_session: AsyncSession,
    user: User,
    clock: MutableClock,
) -> None:
    service = AuthService(db_session, auth_settings, now=clock)
    # Several rounds, because either request may win the race in any one of them.
    for _ in range(5):
        login = await service.login(EMAIL, PASSWORD, IP_A)
        await db_session.commit()

        refreshed, _ = await asyncio.gather(
            _refresh_in_own_session(session_factory, auth_settings, clock, login.refresh_token_raw),
            _logout_in_own_session(session_factory, auth_settings, clock, login.refresh_token_raw),
        )

        assert refreshed in {"OK", "TOKEN_INVALID"}
        with pytest.raises(TokenInvalidError):
            await service.authenticate_access_token(login.access_token)
    assert all(token.revoked_at is not None for token in await _tokens_of(db_session, user))


async def test_access_token_of_a_deactivated_user_is_rejected(
    service: AuthService, db_session: AsyncSession, user: User
) -> None:
    login = await service.login(EMAIL, PASSWORD, IP_A)
    user.is_active = False
    await db_session.commit()

    with pytest.raises(TokenInvalidError):
        await service.authenticate_access_token(login.access_token)


async def test_expired_access_token_is_rejected_as_expired(
    service: AuthService, db_session: AsyncSession, user: User, clock: MutableClock
) -> None:
    login = await service.login(EMAIL, PASSWORD, IP_A)
    await db_session.commit()
    clock.advance(timedelta(minutes=15))

    with pytest.raises(TokenExpiredError):
        await service.authenticate_access_token(login.access_token)


async def test_malformed_access_token_is_rejected(service: AuthService) -> None:
    with pytest.raises(TokenInvalidError):
        await service.authenticate_access_token("not.a.jwt")


# --- me -------------------------------------------------------------------------------------


async def test_get_me_lists_only_the_users_memberships_ordered_by_workspace_name(
    service: AuthService, db_session: AsyncSession, user: User
) -> None:
    outsider = await service.register("outsider@example.com", PASSWORD, "Outsider")
    zeta = Workspace(name="Zeta Works", slug="zeta", owner_id=user.id)
    alpha = Workspace(name="Alpha Labs", slug="alpha", owner_id=outsider.id)
    hidden = Workspace(name="Beta Hidden", slug="beta", owner_id=outsider.id)
    db_session.add_all([zeta, alpha, hidden])
    await db_session.flush()
    db_session.add_all(
        [
            WorkspaceMember(workspace_id=zeta.id, user_id=user.id, role=UserRole.ADMIN),
            WorkspaceMember(workspace_id=alpha.id, user_id=user.id, role=UserRole.VIEWER),
            WorkspaceMember(workspace_id=hidden.id, user_id=outsider.id, role=UserRole.ADMIN),
        ]
    )
    await db_session.commit()

    me = await service.get_me(user)

    assert me.user.id == user.id
    assert [
        (m.workspace_id, m.workspace_name, m.workspace_slug, m.role) for m in me.memberships
    ] == [
        (alpha.id, "Alpha Labs", "alpha", UserRole.VIEWER),
        (zeta.id, "Zeta Works", "zeta", UserRole.ADMIN),
    ]


async def test_get_me_for_a_user_without_workspaces_has_no_memberships(
    service: AuthService, user: User
) -> None:
    assert (await service.get_me(user)).memberships == []
