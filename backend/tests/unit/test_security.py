import base64
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import jwt
import pytest

from app.core.config import Settings
from app.core.security import (
    AccessTokenClaims,
    TokenExpiredError,
    TokenInvalidError,
    create_access_token,
    decode_access_token,
    hash_password,
    hash_refresh_token,
    new_refresh_token,
    password_needs_rehash,
    verify_password,
)
from tests.conftest import make_settings

SECRET = "s" * 48
OTHER_SECRET = "o" * 48
ISSUED_AT = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
USER_ID = UUID("11111111-1111-4111-8111-111111111111")
SESSION_ID = UUID("22222222-2222-4222-8222-222222222222")


@pytest.fixture
def jwt_settings() -> Settings:
    return make_settings(jwt_secret_key=SECRET, access_token_ttl_minutes=15)


def _claims(**overrides: Any) -> dict[str, Any]:
    claims: dict[str, Any] = {
        "sub": str(USER_ID),
        "sid": str(SESSION_ID),
        "iss": "bomviz",
        "aud": "bomviz-api",
        "typ": "access",
        "iat": int(ISSUED_AT.timestamp()),
        "exp": int((ISSUED_AT + timedelta(minutes=15)).timestamp()),
    }
    claims.update(overrides)
    return {key: value for key, value in claims.items() if value is not None}


def _sign(claims: dict[str, Any], secret: str = SECRET, algorithm: str = "HS256") -> str:
    return jwt.encode(claims, secret, algorithm=algorithm)


def _b64(data: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()


def _decode_at(settings: Settings, token: str, minutes_after_issue: int = 1) -> AccessTokenClaims:
    return decode_access_token(
        settings, token, now=ISSUED_AT + timedelta(minutes=minutes_after_issue)
    )


# Passwords


def test_password_round_trip_verifies() -> None:
    password_hash = hash_password("correct horse battery staple")

    assert verify_password(password_hash, "correct horse battery staple")


def test_wrong_password_returns_false() -> None:
    password_hash = hash_password("correct horse battery staple")

    assert verify_password(password_hash, "wrong password!!") is False


def test_invalid_hash_returns_false_instead_of_raising() -> None:
    assert verify_password("not-an-argon2-hash", "whatever password") is False


def test_hash_is_salted_and_never_contains_the_password() -> None:
    first = hash_password("correct horse battery staple")
    second = hash_password("correct horse battery staple")

    assert first != second
    assert "correct horse" not in first
    assert first.startswith("$argon2id$")


def test_fresh_hash_does_not_need_rehash() -> None:
    assert password_needs_rehash(hash_password("correct horse battery staple")) is False


def test_weaker_hash_needs_rehash() -> None:
    from argon2 import PasswordHasher  # noqa: PLC0415 - only this test builds a weak hasher

    weak_hash = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash("pw-123456789")

    assert password_needs_rehash(weak_hash) is True


# Access tokens


def test_access_token_round_trip(jwt_settings: Settings) -> None:
    token, expires_in = create_access_token(jwt_settings, USER_ID, SESSION_ID, now=ISSUED_AT)

    claims = _decode_at(jwt_settings, token)

    assert expires_in == 15 * 60
    assert claims == AccessTokenClaims(
        user_id=USER_ID, session_id=SESSION_ID, expires_at=ISSUED_AT + timedelta(minutes=15)
    )


def test_access_token_carries_the_specified_claims(jwt_settings: Settings) -> None:
    token, _ = create_access_token(jwt_settings, USER_ID, SESSION_ID, now=ISSUED_AT)

    payload = jwt.decode(token, options={"verify_signature": False})

    assert payload == _claims()
    assert jwt.get_unverified_header(token)["alg"] == "HS256"


def test_access_token_without_injected_clock_is_valid_now(jwt_settings: Settings) -> None:
    token, _ = create_access_token(jwt_settings, USER_ID, SESSION_ID)

    assert decode_access_token(jwt_settings, token).user_id == USER_ID


def test_expired_token_raises_token_expired(jwt_settings: Settings) -> None:
    token, _ = create_access_token(jwt_settings, USER_ID, SESSION_ID, now=ISSUED_AT)

    with pytest.raises(TokenExpiredError) as exc_info:
        _decode_at(jwt_settings, token, minutes_after_issue=15)

    assert exc_info.value.status_code == 401
    assert exc_info.value.code == "TOKEN_EXPIRED"


def test_token_minted_at_a_future_injected_clock_decodes_at_that_clock(
    jwt_settings: Settings,
) -> None:
    future = datetime.now(UTC) + timedelta(days=365)
    token, _ = create_access_token(jwt_settings, USER_ID, SESSION_ID, now=future)

    assert decode_access_token(jwt_settings, token, now=future).user_id == USER_ID


def test_token_issued_beyond_the_leeway_after_the_injected_clock_is_invalid(
    jwt_settings: Settings,
) -> None:
    token = _sign(_claims(iat=int((ISSUED_AT + timedelta(seconds=31)).timestamp())))

    with pytest.raises(TokenInvalidError):
        decode_access_token(jwt_settings, token, now=ISSUED_AT)


def test_token_issued_within_the_leeway_after_the_injected_clock_decodes(
    jwt_settings: Settings,
) -> None:
    token = _sign(_claims(iat=int((ISSUED_AT + timedelta(seconds=30)).timestamp())))

    assert decode_access_token(jwt_settings, token, now=ISSUED_AT).user_id == USER_ID


def test_token_with_non_numeric_iat_is_invalid_with_injected_clock(
    jwt_settings: Settings,
) -> None:
    token = _sign(_claims(iat="yesterday"))

    with pytest.raises(TokenInvalidError):
        _decode_at(jwt_settings, token)


def test_expired_token_is_rejected_by_the_real_clock(jwt_settings: Settings) -> None:
    token, _ = create_access_token(
        jwt_settings, USER_ID, SESSION_ID, now=datetime.now(UTC) - timedelta(hours=1)
    )

    with pytest.raises(TokenExpiredError):
        decode_access_token(jwt_settings, token)


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"aud": "someone-else"}, id="wrong-audience"),
        pytest.param({"iss": "evil-issuer"}, id="wrong-issuer"),
        pytest.param({"typ": "refresh"}, id="wrong-type"),
        pytest.param({"sub": "not-a-uuid"}, id="malformed-sub"),
        pytest.param({"sid": "not-a-uuid"}, id="malformed-sid"),
    ],
)
def test_token_with_wrong_claim_is_invalid(
    jwt_settings: Settings, overrides: dict[str, Any]
) -> None:
    token = _sign(_claims(**overrides))

    with pytest.raises(TokenInvalidError) as exc_info:
        _decode_at(jwt_settings, token)

    assert exc_info.value.status_code == 401
    assert exc_info.value.code == "TOKEN_INVALID"


@pytest.mark.parametrize("missing", ["sub", "sid", "iss", "aud", "typ", "iat", "exp"])
def test_token_missing_a_required_claim_is_invalid(jwt_settings: Settings, missing: str) -> None:
    claims = _claims()
    del claims[missing]

    with pytest.raises(TokenInvalidError):
        _decode_at(jwt_settings, _sign(claims))


def test_unsigned_alg_none_token_is_rejected(jwt_settings: Settings) -> None:
    token = f"{_b64({'alg': 'none', 'typ': 'JWT'})}.{_b64(_claims())}."

    with pytest.raises(TokenInvalidError):
        _decode_at(jwt_settings, token)


def test_token_signed_with_another_key_is_rejected(jwt_settings: Settings) -> None:
    token = _sign(_claims(), secret=OTHER_SECRET)

    with pytest.raises(TokenInvalidError):
        _decode_at(jwt_settings, token)


def test_hs256_token_is_rejected_when_settings_pin_another_algorithm() -> None:
    settings = make_settings(jwt_secret_key=SECRET, jwt_algorithm="HS512")
    token = _sign(_claims(), algorithm="HS256")

    with pytest.raises(TokenInvalidError):
        _decode_at(settings, token)


@pytest.mark.parametrize("garbage", ["", "garbage", "a.b.c"])
def test_malformed_token_is_invalid(jwt_settings: Settings, garbage: str) -> None:
    with pytest.raises(TokenInvalidError):
        _decode_at(jwt_settings, garbage)


def test_error_message_never_contains_the_token(jwt_settings: Settings) -> None:
    token = _sign(_claims(aud="someone-else"))

    with pytest.raises(TokenInvalidError) as exc_info:
        _decode_at(jwt_settings, token)

    assert token not in exc_info.value.message
    assert str(USER_ID) not in exc_info.value.message


# Refresh tokens


def test_new_refresh_tokens_are_unique_and_url_safe() -> None:
    tokens = {new_refresh_token() for _ in range(50)}

    assert len(tokens) == 50
    assert all(len(token) >= 43 for token in tokens)
    assert all(
        set(token) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
        for token in tokens
    )


def test_refresh_token_hash_is_sha256_hex_and_deterministic() -> None:
    raw = new_refresh_token()

    digest = hash_refresh_token(raw)

    assert digest == hash_refresh_token(raw)
    assert len(digest) == 64
    assert int(digest, 16) >= 0
    assert raw not in digest


def test_different_refresh_tokens_hash_differently() -> None:
    assert hash_refresh_token(new_refresh_token()) != hash_refresh_token(new_refresh_token())


def test_user_ids_are_independent_per_token(jwt_settings: Settings) -> None:
    other_user = uuid4()
    token, _ = create_access_token(jwt_settings, other_user, SESSION_ID, now=ISSUED_AT)

    assert _decode_at(jwt_settings, token).user_id == other_user
