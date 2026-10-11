"""`/api/v1/auth/*` over HTTP against a real PostgreSQL.

Covers the HTTP contract: status codes, error codes, the refresh cookie's attributes and
lifecycle, the bearer dependency, rate limits (with `FakeRedis`) and the OpenAPI document.
Business rules beneath the routes are covered in `test_auth_service.py`.
"""

import uuid
from collections.abc import AsyncIterator
from http.cookies import SimpleCookie

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.dependencies import get_rate_limiter
from app.main import create_app
from app.models import User, UserRole, Workspace, WorkspaceMember
from app.services.rate_limiter import RateLimiter
from tests.conftest import make_settings
from tests.fakes import FakeRedis

pytestmark = pytest.mark.integration

SECRET = "test-secret-key-that-is-long-enough-for-hs256-0123456789"
COOKIE_NAME = "bomviz_refresh"
EMAIL = "Grace.Hopper@Example.com"
NORMALIZED_EMAIL = "grace.hopper@example.com"
PASSWORD = "a very long passphrase"
WRONG_PASSWORD = "not the passphrase"
FULL_NAME = "Grace Hopper"
AUTH = "/api/v1/auth"


def _settings(database_url: str, *, rate_limit_enabled: bool) -> Settings:
    return make_settings(
        database_url=database_url,
        jwt_secret_key=SECRET,
        refresh_token_ttl_days=7,
        login_free_attempts=4,
        login_base_delay_seconds=30,
        rate_limit_enabled=rate_limit_enabled,
    )


@pytest.fixture
def db_settings(migrated_database_url: str, truncate_tables: None) -> Settings:
    return _settings(migrated_database_url, rate_limit_enabled=False)


async def _client_for(app: FastAPI, client_ip: str = "203.0.113.10") -> AsyncIterator[AsyncClient]:
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, client=(client_ip, 50000))
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client


@pytest.fixture
def limited_app(migrated_database_url: str, truncate_tables: None) -> FastAPI:
    """An app with rate limiting enabled, counting in an in-memory Redis."""
    settings = _settings(migrated_database_url, rate_limit_enabled=True)
    app = create_app(settings)
    fake_redis = FakeRedis()
    app.dependency_overrides[get_rate_limiter] = lambda: RateLimiter(fake_redis, settings)
    return app


@pytest.fixture
async def limited_client(limited_app: FastAPI) -> AsyncIterator[AsyncClient]:
    async for client in _client_for(limited_app):
        yield client


# Helpers


async def _register(client: AsyncClient, email: str = EMAIL) -> Response:
    return await client.post(
        f"{AUTH}/register", json={"email": email, "password": PASSWORD, "full_name": FULL_NAME}
    )


async def _login(client: AsyncClient, email: str = EMAIL, password: str = PASSWORD) -> Response:
    response = await client.post(f"{AUTH}/login", json={"email": email, "password": password})
    # Tests pass cookies explicitly so each request's cookie is unambiguous.
    client.cookies.clear()
    return response


def _set_cookie(response: Response) -> SimpleCookie:
    headers = response.headers.get_list("set-cookie")
    assert len(headers) == 1
    cookie = SimpleCookie()
    cookie.load(headers[0])
    return cookie


def _refresh_cookie_value(response: Response) -> str:
    return _set_cookie(response)[COOKIE_NAME].value


def _assert_clears_refresh_cookie(response: Response) -> None:
    morsel = _set_cookie(response)[COOKIE_NAME]
    assert morsel.value == ""
    assert morsel["max-age"] == "0"
    assert morsel["path"] == "/api/v1/auth"


def _cookie_header(raw_token: str) -> dict[str, str]:
    return {"Cookie": f"{COOKIE_NAME}={raw_token}"}


def _bearer(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


async def _refresh(client: AsyncClient, raw_token: str) -> Response:
    return await client.post(f"{AUTH}/refresh", headers=_cookie_header(raw_token))


def _error_code(response: Response) -> str:
    code: str = response.json()["error"]["code"]
    return code


def _error_without_request_id(response: Response) -> dict[str, object]:
    error: dict[str, object] = dict(response.json()["error"])
    error.pop("request_id")
    return error


# Register


async def test_register_creates_the_account_and_returns_the_public_user(
    db_client: AsyncClient,
) -> None:
    response = await db_client.post(
        f"{AUTH}/register",
        json={"email": EMAIL, "password": PASSWORD, "full_name": "  Grace Hopper  "},
    )

    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"id", "email", "full_name", "is_active", "created_at"}
    assert body["email"] == NORMALIZED_EMAIL
    assert body["full_name"] == "Grace Hopper"
    assert body["is_active"] is True
    assert "set-cookie" not in response.headers


async def test_register_rejects_a_short_password(db_client: AsyncClient) -> None:
    response = await db_client.post(
        f"{AUTH}/register", json={"email": EMAIL, "password": "short", "full_name": FULL_NAME}
    )

    assert response.status_code == 422
    assert _error_code(response) == "VALIDATION_ERROR"
    assert [d["field"] for d in response.json()["error"]["details"]] == ["body.password"]


async def test_register_rejects_a_malformed_email(db_client: AsyncClient) -> None:
    response = await db_client.post(
        f"{AUTH}/register",
        json={"email": "not-an-email", "password": PASSWORD, "full_name": FULL_NAME},
    )

    assert response.status_code == 422
    assert [d["field"] for d in response.json()["error"]["details"]] == ["body.email"]


async def test_register_rejects_a_blank_full_name(db_client: AsyncClient) -> None:
    response = await db_client.post(
        f"{AUTH}/register", json={"email": EMAIL, "password": PASSWORD, "full_name": "   "}
    )

    assert response.status_code == 422
    assert [d["field"] for d in response.json()["error"]["details"]] == ["body.full_name"]


async def test_register_rejects_unknown_fields(db_client: AsyncClient) -> None:
    response = await db_client.post(
        f"{AUTH}/register",
        json={"email": EMAIL, "password": PASSWORD, "full_name": FULL_NAME, "is_active": False},
    )

    assert response.status_code == 422
    assert [d["field"] for d in response.json()["error"]["details"]] == ["body.is_active"]


async def test_register_rejects_a_password_equal_to_the_email(db_client: AsyncClient) -> None:
    email = "long.address@example.com"
    response = await db_client.post(
        f"{AUTH}/register", json={"email": email, "password": email, "full_name": FULL_NAME}
    )

    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["type"] == "password_equals_email"


async def test_register_with_a_taken_email_is_a_conflict(db_client: AsyncClient) -> None:
    await _register(db_client)

    response = await _register(db_client, email=EMAIL.upper())

    assert response.status_code == 409
    assert _error_code(response) == "EMAIL_TAKEN"


# Login


async def test_login_returns_an_access_token_and_the_user(db_client: AsyncClient) -> None:
    registered = (await _register(db_client)).json()

    response = await _login(db_client)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"access_token", "token_type", "expires_in", "user"}
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 15 * 60
    assert body["user"] == registered


async def test_login_sets_a_hardened_refresh_cookie(db_client: AsyncClient) -> None:
    await _register(db_client)

    response = await _login(db_client)

    morsel = _set_cookie(response)[COOKIE_NAME]
    assert morsel.value
    assert morsel["httponly"] is True
    assert morsel["samesite"] == "strict"
    assert morsel["path"] == "/api/v1/auth"
    # Max-Age is the session's remaining lifetime, computed at response time.
    assert 7 * 24 * 60 * 60 - 60 <= int(morsel["max-age"]) <= 7 * 24 * 60 * 60


async def test_login_with_a_wrong_password_is_rejected(db_client: AsyncClient) -> None:
    await _register(db_client)

    response = await _login(db_client, password=WRONG_PASSWORD)

    assert response.status_code == 401
    assert _error_code(response) == "INVALID_CREDENTIALS"
    assert "set-cookie" not in response.headers


async def test_login_rejects_unknown_fields(db_client: AsyncClient) -> None:
    response = await db_client.post(
        f"{AUTH}/login", json={"email": EMAIL, "password": PASSWORD, "remember": True}
    )

    assert response.status_code == 422


async def test_throttled_login_carries_retry_after_and_hides_whether_the_account_exists(
    db_client: AsyncClient,
) -> None:
    await _register(db_client)
    unknown_email = "nobody.here@example.com"
    for email in (EMAIL, unknown_email):
        for _ in range(4):
            assert (await _login(db_client, email, WRONG_PASSWORD)).status_code == 401

    known = await _login(db_client, EMAIL, PASSWORD)
    unknown = await _login(db_client, unknown_email, PASSWORD)

    for response in (known, unknown):
        assert response.status_code == 429
        assert _error_code(response) == "LOGIN_THROTTLED"
        assert 0 < int(response.headers["retry-after"]) <= 30
    assert _error_without_request_id(known) == _error_without_request_id(unknown)


# Refresh


async def test_refresh_without_a_cookie_is_unauthenticated(db_client: AsyncClient) -> None:
    response = await db_client.post(f"{AUTH}/refresh")

    assert response.status_code == 401
    assert _error_code(response) == "UNAUTHENTICATED"


async def test_refresh_rotates_the_cookie_and_issues_a_working_access_token(
    db_client: AsyncClient,
) -> None:
    await _register(db_client)
    first_cookie = _refresh_cookie_value(await _login(db_client))

    response = await _refresh(db_client, first_cookie)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"access_token", "token_type", "expires_in"}
    assert body["token_type"] == "bearer"
    rotated = _set_cookie(response)[COOKIE_NAME]
    assert rotated.value not in ("", first_cookie)
    assert rotated["httponly"] is True
    assert rotated["samesite"] == "strict"
    me = await db_client.get(f"{AUTH}/me", headers=_bearer(body["access_token"]))
    assert me.status_code == 200


async def test_refresh_never_extends_the_cookie_beyond_the_session_lifetime(
    db_client: AsyncClient,
) -> None:
    await _register(db_client)
    login = await _login(db_client)
    login_max_age = int(_set_cookie(login)[COOKIE_NAME]["max-age"])

    refreshed = await _refresh(db_client, _refresh_cookie_value(login))

    assert int(_set_cookie(refreshed)[COOKIE_NAME]["max-age"]) <= login_max_age


async def test_rotated_cookie_keeps_working(db_client: AsyncClient) -> None:
    await _register(db_client)
    first_cookie = _refresh_cookie_value(await _login(db_client))
    second_cookie = _refresh_cookie_value(await _refresh(db_client, first_cookie))

    response = await _refresh(db_client, second_cookie)

    assert response.status_code == 200


async def test_reusing_a_rotated_cookie_is_rejected_and_clears_the_cookie(
    db_client: AsyncClient,
) -> None:
    await _register(db_client)
    first_cookie = _refresh_cookie_value(await _login(db_client))
    second_cookie = _refresh_cookie_value(await _refresh(db_client, first_cookie))

    reused = await _refresh(db_client, first_cookie)

    assert reused.status_code == 401
    assert _error_code(reused) == "TOKEN_REUSED"
    _assert_clears_refresh_cookie(reused)
    # Reuse revokes every session, including the legitimate successor - which, revoked
    # rather than rotated, is then simply invalid.
    assert _error_code(await _refresh(db_client, second_cookie)) == "TOKEN_INVALID"


async def test_refresh_with_an_unknown_cookie_is_invalid_and_clears_the_cookie(
    db_client: AsyncClient,
) -> None:
    response = await _refresh(db_client, "not-a-real-refresh-token")

    assert response.status_code == 401
    assert _error_code(response) == "TOKEN_INVALID"
    _assert_clears_refresh_cookie(response)


# Logout


async def test_logout_clears_the_cookie_and_invalidates_the_session(
    db_client: AsyncClient,
) -> None:
    await _register(db_client)
    login = await _login(db_client)
    access_token = login.json()["access_token"]

    response = await db_client.post(
        f"{AUTH}/logout", headers=_cookie_header(_refresh_cookie_value(login))
    )

    assert response.status_code == 204
    assert response.content == b""
    _assert_clears_refresh_cookie(response)
    me = await db_client.get(f"{AUTH}/me", headers=_bearer(access_token))
    assert me.status_code == 401
    assert _error_code(me) == "TOKEN_INVALID"


async def test_refresh_with_a_logged_out_cookie_is_invalid_and_keeps_other_sessions(
    db_client: AsyncClient,
) -> None:
    await _register(db_client)
    signed_out_cookie = _refresh_cookie_value(await _login(db_client))
    other_cookie = _refresh_cookie_value(await _login(db_client))
    await db_client.post(f"{AUTH}/logout", headers=_cookie_header(signed_out_cookie))
    db_client.cookies.clear()

    response = await _refresh(db_client, signed_out_cookie)

    assert response.status_code == 401
    assert _error_code(response) == "TOKEN_INVALID"
    _assert_clears_refresh_cookie(response)
    assert (await _refresh(db_client, other_cookie)).status_code == 200


async def test_logout_without_a_cookie_succeeds(db_client: AsyncClient) -> None:
    response = await db_client.post(f"{AUTH}/logout")

    assert response.status_code == 204
    _assert_clears_refresh_cookie(response)


# Current user


async def test_me_returns_the_user_and_their_memberships(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    registered = (await _register(db_client)).json()
    owner = await db_session.get(User, uuid.UUID(registered["id"]))
    assert owner is not None
    workspace = Workspace(name="Analytical Engines", slug="analytical-engines", owner_id=owner.id)
    db_session.add(workspace)
    await db_session.flush()
    db_session.add(
        WorkspaceMember(workspace_id=workspace.id, user_id=owner.id, role=UserRole.ADMIN)
    )
    await db_session.commit()
    access_token = (await _login(db_client)).json()["access_token"]

    response = await db_client.get(f"{AUTH}/me", headers=_bearer(access_token))

    assert response.status_code == 200
    body = response.json()
    assert {key: value for key, value in body.items() if key != "memberships"} == registered
    assert body["memberships"] == [
        {
            "workspace_id": str(workspace.id),
            "workspace_name": "Analytical Engines",
            "workspace_slug": "analytical-engines",
            "role": "ADMIN",
        }
    ]


async def test_me_without_a_token_is_unauthenticated(db_client: AsyncClient) -> None:
    response = await db_client.get(f"{AUTH}/me")

    assert response.status_code == 401
    assert _error_code(response) == "UNAUTHENTICATED"
    assert response.headers["www-authenticate"] == "Bearer"


async def test_me_with_a_non_bearer_scheme_is_unauthenticated(db_client: AsyncClient) -> None:
    response = await db_client.get(f"{AUTH}/me", headers={"Authorization": "Basic dXNlcjpwdw=="})

    assert response.status_code == 401
    assert _error_code(response) == "UNAUTHENTICATED"


async def test_me_with_a_garbage_token_is_invalid(db_client: AsyncClient) -> None:
    response = await db_client.get(f"{AUTH}/me", headers=_bearer("garbage.token.value"))

    assert response.status_code == 401
    assert _error_code(response) == "TOKEN_INVALID"
    assert response.headers["www-authenticate"] == "Bearer"


async def test_no_auth_response_contains_the_password_hash(db_client: AsyncClient) -> None:
    register = await _register(db_client)
    login = await _login(db_client)
    me = await db_client.get(f"{AUTH}/me", headers=_bearer(login.json()["access_token"]))

    for response in (register, login, me):
        assert response.status_code in (200, 201)
        assert "password_hash" not in response.text
        assert "argon2" not in response.text


# Rate limits


async def test_register_is_rate_limited_per_ip(limited_client: AsyncClient) -> None:
    for attempt in range(5):
        assert (await _register(limited_client)).status_code == (201 if attempt == 0 else 409)

    response = await _register(limited_client)

    assert response.status_code == 429
    assert _error_code(response) == "RATE_LIMITED"
    assert 0 < int(response.headers["retry-after"]) <= 3600


async def test_register_rate_limit_does_not_affect_other_ips(limited_app: FastAPI) -> None:
    async for client_a in _client_for(limited_app, "198.51.100.1"):
        for _ in range(6):
            await _register(client_a)
        assert (await _register(client_a)).status_code == 429
    async for client_b in _client_for(limited_app, "198.51.100.2"):
        assert (await _register(client_b)).status_code == 409


async def test_login_is_rate_limited_per_ip(limited_client: AsyncClient) -> None:
    for _ in range(30):
        assert (await _login(limited_client, password=WRONG_PASSWORD)).status_code in (401, 429)

    response = await _login(limited_client)

    assert response.status_code == 429
    assert _error_code(response) == "RATE_LIMITED"
    assert 0 < int(response.headers["retry-after"]) <= 900


async def test_refresh_is_rate_limited_per_ip(limited_client: AsyncClient) -> None:
    for _ in range(60):
        assert (await limited_client.post(f"{AUTH}/refresh")).status_code == 401

    response = await limited_client.post(f"{AUTH}/refresh")

    assert response.status_code == 429
    assert _error_code(response) == "RATE_LIMITED"
    assert 0 < int(response.headers["retry-after"]) <= 900


# OpenAPI


async def test_openapi_documents_the_auth_endpoints_and_their_errors(
    db_client: AsyncClient,
) -> None:
    document = (await db_client.get("/api/v1/openapi.json")).json()
    paths = document["paths"]

    assert {path for path in paths if path.startswith(AUTH)} == {
        f"{AUTH}/register",
        f"{AUTH}/login",
        f"{AUTH}/refresh",
        f"{AUTH}/logout",
        f"{AUTH}/me",
    }
    expected_errors = {
        ("register", "post"): {"409", "422", "429"},
        ("login", "post"): {"401", "422", "429"},
        ("refresh", "post"): {"401", "429"},
        ("me", "get"): {"401"},
    }
    error_ref = "#/components/schemas/ErrorResponse"
    for (name, method), codes in expected_errors.items():
        responses = paths[f"{AUTH}/{name}"][method]["responses"]
        assert codes <= set(responses)
        for code in codes:
            assert responses[code]["content"]["application/json"]["schema"]["$ref"] == error_ref
    assert "204" in paths[f"{AUTH}/logout"]["post"]["responses"]
