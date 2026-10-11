"""`/api/v1/workspaces/*` over HTTP against a real PostgreSQL.

Covers creation and slug rules, listing, workspace-scoped authorization (404 for non-members,
403 for missing permissions), member management, the `LAST_ADMIN` guard (including two
concurrent demotions) and the audit rows written for each change (amendment B for removal).
"""

import asyncio
import re
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.dependencies import DbSessionDep, get_rate_limiter, get_workspace_service
from app.main import create_app
from app.models import AuditLog, User, UserRole, Workspace, WorkspaceMember
from app.services.rate_limiter import RateLimiter
from app.services.workspace_service import SLUG_PATTERN, WorkspaceService
from tests.conftest import make_settings
from tests.fakes import FakeRedis

pytestmark = pytest.mark.integration

SECRET = "test-secret-key-that-is-long-enough-for-hs256-0123456789"
PASSWORD = "a very long passphrase"
API = "/api/v1"
WORKSPACES = f"{API}/workspaces"


@pytest.fixture
def db_settings(migrated_database_url: str, truncate_tables: None) -> Settings:
    return make_settings(
        database_url=migrated_database_url, jwt_secret_key=SECRET, rate_limit_enabled=False
    )


@pytest.fixture
async def limited_client(
    migrated_database_url: str, truncate_tables: None
) -> AsyncIterator[AsyncClient]:
    """A client for an app with rate limiting enabled, counting in an in-memory Redis.

    Sign-up and login limits are per IP and generous enough for these tests' few accounts.
    """
    settings = make_settings(
        database_url=migrated_database_url, jwt_secret_key=SECRET, rate_limit_enabled=True
    )
    app = create_app(settings)
    fake_redis = FakeRedis()
    app.dependency_overrides[get_rate_limiter] = lambda: RateLimiter(fake_redis, settings)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client


# Helpers


@dataclass(frozen=True)
class Actor:
    id: str
    email: str
    headers: dict[str, str]


async def _signup(client: AsyncClient, email: str, full_name: str = "Ada Lovelace") -> Actor:
    registered = await client.post(
        f"{API}/auth/register",
        json={"email": email, "password": PASSWORD, "full_name": full_name},
    )
    assert registered.status_code == 201, registered.text
    login = await client.post(f"{API}/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200, login.text
    client.cookies.clear()
    return Actor(
        id=registered.json()["id"],
        email=email,
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    )


async def _create_workspace(
    client: AsyncClient, actor: Actor, name: str = "Acme Robotics", slug: str | None = None
) -> dict[str, str]:
    body: dict[str, str] = {"name": name}
    if slug is not None:
        body["slug"] = slug
    response = await client.post(WORKSPACES, json=body, headers=actor.headers)
    assert response.status_code == 201, response.text
    workspace: dict[str, str] = response.json()
    return workspace


async def _add_member(
    client: AsyncClient, actor: Actor, workspace_id: str, email: str, role: str
) -> Response:
    return await client.post(
        f"{WORKSPACES}/{workspace_id}/members",
        json={"email": email, "role": role},
        headers=actor.headers,
    )


async def _added_member_id(
    client: AsyncClient, actor: Actor, workspace_id: str, email: str, role: str
) -> str:
    response = await _add_member(client, actor, workspace_id, email, role)
    assert response.status_code == 201, response.text
    member_id: str = response.json()["id"]
    return member_id


async def _member_id_of(client: AsyncClient, actor: Actor, workspace_id: str, user_id: str) -> str:
    response = await client.get(f"{WORKSPACES}/{workspace_id}/members", headers=actor.headers)
    assert response.status_code == 200, response.text
    for item in response.json()["items"]:
        if item["user_id"] == user_id:
            member_id: str = item["id"]
            return member_id
    raise AssertionError(f"user {user_id} is not a member of {workspace_id}")


async def _patch_role(
    client: AsyncClient, actor: Actor, workspace_id: str, member_id: str, role: str
) -> Response:
    return await client.patch(
        f"{WORKSPACES}/{workspace_id}/members/{member_id}",
        json={"role": role},
        headers=actor.headers,
    )


async def _delete_member(
    client: AsyncClient, actor: Actor, workspace_id: str, member_id: str
) -> Response:
    return await client.delete(
        f"{WORKSPACES}/{workspace_id}/members/{member_id}", headers=actor.headers
    )


def _error(response: Response) -> tuple[str, str]:
    error = response.json()["error"]
    return error["code"], error["message"]


async def _audits(session: AsyncSession, action: str) -> list[AuditLog]:
    session.expire_all()
    result = await session.execute(
        select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.created_at)
    )
    return list(result.scalars().all())


async def _admin_count(session: AsyncSession, workspace_id: str) -> int:
    count = await session.scalar(
        select(func.count())
        .select_from(WorkspaceMember)
        .where(
            WorkspaceMember.workspace_id == uuid.UUID(workspace_id),
            WorkspaceMember.role == UserRole.ADMIN,
        )
    )
    return count or 0


@pytest.fixture
def suffixes(db_app: FastAPI) -> Iterator[list[str]]:
    """Make generated slug suffixes deterministic: they are popped from the returned list."""
    queue: list[str] = []

    def service(session: DbSessionDep) -> WorkspaceService:
        return WorkspaceService(session, slug_suffix=lambda: queue.pop(0))

    db_app.dependency_overrides[get_workspace_service] = service
    yield queue
    db_app.dependency_overrides.pop(get_workspace_service, None)


# Create


async def test_create_returns_the_workspace_with_the_callers_admin_role(
    db_client: AsyncClient,
) -> None:
    ada = await _signup(db_client, "ada@example.com")

    response = await db_client.post(
        WORKSPACES, json={"name": "  Acme Robotics  ", "slug": "acme"}, headers=ada.headers
    )

    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"id", "name", "slug", "owner_id", "role", "created_at"}
    assert body["name"] == "Acme Robotics"
    assert body["slug"] == "acme"
    assert body["owner_id"] == ada.id
    assert body["role"] == "ADMIN"


async def test_create_makes_the_creator_an_admin_member(db_client: AsyncClient) -> None:
    ada = await _signup(db_client, "ada@example.com")
    workspace = await _create_workspace(db_client, ada)

    response = await db_client.get(f"{WORKSPACES}/{workspace['id']}/members", headers=ada.headers)

    assert [(m["user_id"], m["role"]) for m in response.json()["items"]] == [(ada.id, "ADMIN")]


async def test_create_is_audited(db_client: AsyncClient, db_session: AsyncSession) -> None:
    ada = await _signup(db_client, "ada@example.com")
    workspace = await _create_workspace(db_client, ada, slug="acme")

    [audit] = await _audits(db_session, "WORKSPACE_CREATED")

    assert audit.actor_id == uuid.UUID(ada.id)
    assert audit.workspace_id == uuid.UUID(workspace["id"])
    assert audit.entity_type == "workspace"
    assert audit.entity_id == uuid.UUID(workspace["id"])


async def test_create_requires_authentication(db_client: AsyncClient) -> None:
    response = await db_client.post(WORKSPACES, json={"name": "Acme"})

    assert response.status_code == 401
    assert _error(response)[0] == "UNAUTHENTICATED"


@pytest.mark.parametrize(
    "body",
    [
        {"name": ""},
        {"name": "   "},
        {"name": "x" * 101},
        {"name": "Acme", "owner_id": str(uuid.uuid4())},
    ],
    ids=["empty-name", "blank-name", "long-name", "unknown-field"],
)
async def test_create_rejects_an_invalid_body(db_client: AsyncClient, body: dict[str, str]) -> None:
    ada = await _signup(db_client, "ada@example.com")

    response = await db_client.post(WORKSPACES, json=body, headers=ada.headers)

    assert response.status_code == 422
    assert _error(response)[0] == "VALIDATION_ERROR"


@pytest.mark.parametrize(
    "slug",
    ["ab", "a" * 49, "Acme", "acme corp", "-acme", "acme-", "acme--corp", "acmé"],
)
async def test_create_rejects_a_malformed_slug(db_client: AsyncClient, slug: str) -> None:
    ada = await _signup(db_client, "ada@example.com")

    response = await db_client.post(
        WORKSPACES, json={"name": "Acme", "slug": slug}, headers=ada.headers
    )

    assert response.status_code == 422


async def test_create_accepts_slugs_at_the_length_limits(db_client: AsyncClient) -> None:
    ada = await _signup(db_client, "ada@example.com")

    shortest = await _create_workspace(db_client, ada, slug="abc")
    longest = await _create_workspace(db_client, ada, slug="a" * 48)

    assert (shortest["slug"], longest["slug"]) == ("abc", "a" * 48)


# Slugs


async def test_a_taken_supplied_slug_is_a_conflict(db_client: AsyncClient) -> None:
    ada = await _signup(db_client, "ada@example.com")
    await _create_workspace(db_client, ada, slug="acme")

    response = await db_client.post(
        WORKSPACES, json={"name": "Other", "slug": "acme"}, headers=ada.headers
    )

    assert response.status_code == 409
    assert _error(response)[0] == "SLUG_TAKEN"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Acme Robotics", "acme-robotics"),
        ("  Ünïcode Wörks & Co!  ", "unicode-works-co"),
        ("!!!", "workspace"),
        ("日本語", "workspace"),
        ("word " * 20, ("word-" * 8).rstrip("-")),
    ],
    ids=["simple", "accents-and-punctuation", "punctuation-only", "non-latin", "long"],
)
async def test_an_omitted_slug_is_derived_from_the_name(
    db_client: AsyncClient, name: str, expected: str
) -> None:
    ada = await _signup(db_client, "ada@example.com")

    workspace = await _create_workspace(db_client, ada, name=name)

    assert workspace["slug"] == expected
    assert len(workspace["slug"]) <= 40


@pytest.mark.parametrize(
    ("name", "expected"),
    [("AB", "ab-1a2b"), ("X", "x-1a2b"), ("é!", "e-1a2b")],
    ids=["two-chars", "one-char", "one-char-after-ascii-folding"],
)
async def test_a_derived_slug_shorter_than_three_characters_always_gets_a_suffix(
    db_client: AsyncClient, suffixes: list[str], name: str, expected: str
) -> None:
    ada = await _signup(db_client, "ada@example.com")
    suffixes.append("1a2b")

    workspace = await _create_workspace(db_client, ada, name=name)

    assert workspace["slug"] == expected
    assert suffixes == []


async def test_a_derived_slug_of_exactly_three_characters_is_used_bare(
    db_client: AsyncClient, suffixes: list[str]
) -> None:
    ada = await _signup(db_client, "ada@example.com")

    workspace = await _create_workspace(db_client, ada, name="Abc")

    assert workspace["slug"] == "abc"


@pytest.mark.parametrize("name", ["AB", "X", "!!!", "Acme Robotics", "word " * 20])
async def test_derived_slugs_satisfy_the_supplied_slug_rules(
    db_client: AsyncClient, name: str
) -> None:
    ada = await _signup(db_client, "ada@example.com")

    slug = (await _create_workspace(db_client, ada, name=name))["slug"]

    assert re.fullmatch(SLUG_PATTERN, slug)
    assert 3 <= len(slug) <= 48


async def test_a_short_derived_slug_that_collides_retries_with_a_new_suffix(
    db_client: AsyncClient, suffixes: list[str]
) -> None:
    ada = await _signup(db_client, "ada@example.com")
    suffixes.append("1a2b")
    await _create_workspace(db_client, ada, name="AB")
    suffixes.extend(["1a2b", "beef"])

    workspace = await _create_workspace(db_client, ada, name="AB")

    assert workspace["slug"] == "ab-beef"


async def test_a_derived_slug_that_collides_gets_a_random_hex_suffix(
    db_client: AsyncClient,
) -> None:
    ada = await _signup(db_client, "ada@example.com")
    await _create_workspace(db_client, ada, name="Acme")

    workspace = await _create_workspace(db_client, ada, name="Acme")

    assert re.fullmatch(r"acme-[0-9a-f]{4}", workspace["slug"])


async def test_derived_slug_retries_with_a_new_suffix_after_each_collision(
    db_client: AsyncClient, db_session: AsyncSession, suffixes: list[str]
) -> None:
    ada = await _signup(db_client, "ada@example.com")
    await _create_workspace(db_client, ada, name="Acme")
    suffixes.append("0000")
    await _create_workspace(db_client, ada, name="Acme")  # takes acme-0000
    suffixes.extend(["0000", "0000", "beef"])

    workspace = await _create_workspace(db_client, ada, name="Acme")

    assert workspace["slug"] == "acme-beef"
    assert workspace["role"] == "ADMIN"
    assert len(await _audits(db_session, "WORKSPACE_CREATED")) == 3


async def test_derived_slug_gives_up_after_five_suffixed_attempts(
    db_client: AsyncClient, db_session: AsyncSession, suffixes: list[str]
) -> None:
    ada = await _signup(db_client, "ada@example.com")
    await _create_workspace(db_client, ada, name="Acme")
    suffixes.append("0000")
    await _create_workspace(db_client, ada, name="Acme")
    suffixes.extend(["0000"] * 5 + ["beef"])

    response = await db_client.post(WORKSPACES, json={"name": "Acme"}, headers=ada.headers)

    assert response.status_code == 409
    assert _error(response)[0] == "SLUG_TAKEN"
    assert suffixes == ["beef"]
    assert await db_session.scalar(select(func.count()).select_from(Workspace)) == 2


# List and get


async def test_list_returns_only_the_callers_workspaces_with_their_roles(
    db_client: AsyncClient,
) -> None:
    ada = await _signup(db_client, "ada@example.com")
    bob = await _signup(db_client, "bob@example.com")
    zeta = await _create_workspace(db_client, ada, name="Zeta")
    await _create_workspace(db_client, bob, name="Bob's private workspace")
    beta = await _create_workspace(db_client, bob, name="Beta")
    await _add_member(db_client, bob, beta["id"], ada.email, "VIEWER")

    response = await db_client.get(WORKSPACES, headers=ada.headers)

    assert response.status_code == 200
    body = response.json()
    assert [(w["id"], w["role"]) for w in body["items"]] == [
        (beta["id"], "VIEWER"),
        (zeta["id"], "ADMIN"),
    ]
    assert (body["total"], body["limit"], body["offset"]) == (2, 50, 0)


async def test_list_paginates_with_the_full_total(db_client: AsyncClient) -> None:
    ada = await _signup(db_client, "ada@example.com")
    for name in ["Alpha", "Bravo", "Charlie"]:
        await _create_workspace(db_client, ada, name=name)

    response = await db_client.get(f"{WORKSPACES}?limit=2&offset=1", headers=ada.headers)

    body = response.json()
    assert [w["name"] for w in body["items"]] == ["Bravo", "Charlie"]
    assert (body["total"], body["limit"], body["offset"]) == (3, 2, 1)


@pytest.mark.parametrize("query", ["limit=0", "limit=201", "offset=-1"])
async def test_list_rejects_out_of_range_pagination(db_client: AsyncClient, query: str) -> None:
    ada = await _signup(db_client, "ada@example.com")

    response = await db_client.get(f"{WORKSPACES}?{query}", headers=ada.headers)

    assert response.status_code == 422


async def test_list_requires_authentication(db_client: AsyncClient) -> None:
    response = await db_client.get(WORKSPACES)

    assert response.status_code == 401


async def test_get_returns_the_workspace_with_the_callers_role(db_client: AsyncClient) -> None:
    ada = await _signup(db_client, "ada@example.com")
    bob = await _signup(db_client, "bob@example.com")
    workspace = await _create_workspace(db_client, ada)
    await _add_member(db_client, ada, workspace["id"], bob.email, "ENGINEER")

    response = await db_client.get(f"{WORKSPACES}/{workspace['id']}", headers=bob.headers)

    assert response.status_code == 200
    assert response.json() == {**workspace, "role": "ENGINEER"}


async def test_non_member_and_nonexistent_workspace_get_the_same_404(
    db_client: AsyncClient,
) -> None:
    ada = await _signup(db_client, "ada@example.com")
    mallory = await _signup(db_client, "mallory@example.com")
    workspace = await _create_workspace(db_client, ada)

    foreign = await db_client.get(f"{WORKSPACES}/{workspace['id']}", headers=mallory.headers)
    missing = await db_client.get(f"{WORKSPACES}/{uuid.uuid4()}", headers=mallory.headers)

    assert foreign.status_code == missing.status_code == 404
    assert _error(foreign) == _error(missing) == ("NOT_FOUND", "Workspace not found.")


async def test_a_malformed_workspace_id_is_a_validation_error(db_client: AsyncClient) -> None:
    ada = await _signup(db_client, "ada@example.com")

    response = await db_client.get(f"{WORKSPACES}/not-a-uuid", headers=ada.headers)

    assert response.status_code == 422


# Members: read


async def test_members_are_listed_by_full_name_then_email(db_client: AsyncClient) -> None:
    admin = await _signup(db_client, "zed@example.com", full_name="Zed Admin")
    workspace = await _create_workspace(db_client, admin)
    for email, name in [
        ("carol@example.com", "Carol"),
        ("b-amy@example.com", "Amy"),
        ("a-amy@example.com", "Amy"),
    ]:
        await _signup(db_client, email, full_name=name)
        await _add_member(db_client, admin, workspace["id"], email, "VIEWER")

    response = await db_client.get(f"{WORKSPACES}/{workspace['id']}/members", headers=admin.headers)

    body = response.json()
    assert [(m["full_name"], m["email"]) for m in body["items"]] == [
        ("Amy", "a-amy@example.com"),
        ("Amy", "b-amy@example.com"),
        ("Carol", "carol@example.com"),
        ("Zed Admin", "zed@example.com"),
    ]
    assert set(body["items"][0]) == {"id", "user_id", "email", "full_name", "role", "created_at"}
    assert body["total"] == 4


async def test_members_list_paginates_with_the_full_total(db_client: AsyncClient) -> None:
    admin = await _signup(db_client, "admin@example.com", full_name="A Admin")
    workspace = await _create_workspace(db_client, admin)
    for email, name in [("b@example.com", "B"), ("c@example.com", "C")]:
        await _signup(db_client, email, full_name=name)
        await _add_member(db_client, admin, workspace["id"], email, "VIEWER")

    response = await db_client.get(
        f"{WORKSPACES}/{workspace['id']}/members?limit=1&offset=1", headers=admin.headers
    )

    body = response.json()
    assert [m["full_name"] for m in body["items"]] == ["B"]
    assert (body["total"], body["limit"], body["offset"]) == (3, 1, 1)


async def test_a_viewer_can_list_members(db_client: AsyncClient) -> None:
    admin = await _signup(db_client, "admin@example.com")
    viewer = await _signup(db_client, "viewer@example.com")
    workspace = await _create_workspace(db_client, admin)
    await _add_member(db_client, admin, workspace["id"], viewer.email, "VIEWER")

    response = await db_client.get(
        f"{WORKSPACES}/{workspace['id']}/members", headers=viewer.headers
    )

    assert response.status_code == 200
    assert response.json()["total"] == 2


# Members: authorization


async def test_a_non_member_gets_404_for_every_member_endpoint(db_client: AsyncClient) -> None:
    admin = await _signup(db_client, "admin@example.com")
    mallory = await _signup(db_client, "mallory@example.com")
    workspace = await _create_workspace(db_client, admin)
    admin_member = await _member_id_of(db_client, admin, workspace["id"], admin.id)
    base = f"{WORKSPACES}/{workspace['id']}/members"

    responses = [
        await db_client.get(base, headers=mallory.headers),
        await _add_member(db_client, mallory, workspace["id"], mallory.email, "ADMIN"),
        await _patch_role(db_client, mallory, workspace["id"], admin_member, "VIEWER"),
        await _delete_member(db_client, mallory, workspace["id"], admin_member),
    ]

    for response in responses:
        assert response.status_code == 404
        assert _error(response) == ("NOT_FOUND", "Workspace not found.")
    members = await db_client.get(base, headers=admin.headers)
    assert [(m["user_id"], m["role"]) for m in members.json()["items"]] == [(admin.id, "ADMIN")]


@pytest.mark.parametrize("role", ["ENGINEER", "PROCUREMENT", "PRODUCTION", "VIEWER"])
async def test_non_admin_roles_cannot_add_members(db_client: AsyncClient, role: str) -> None:
    admin = await _signup(db_client, "admin@example.com")
    member = await _signup(db_client, "member@example.com")
    await _signup(db_client, "newcomer@example.com")
    workspace = await _create_workspace(db_client, admin)
    await _add_member(db_client, admin, workspace["id"], member.email, role)

    response = await _add_member(db_client, member, workspace["id"], "newcomer@example.com", role)

    assert response.status_code == 403
    assert _error(response)[0] == "FORBIDDEN"


async def test_a_viewer_cannot_change_roles_or_remove_members(db_client: AsyncClient) -> None:
    admin = await _signup(db_client, "admin@example.com")
    viewer = await _signup(db_client, "viewer@example.com")
    workspace = await _create_workspace(db_client, admin)
    viewer_member = await _added_member_id(
        db_client, admin, workspace["id"], viewer.email, "VIEWER"
    )

    patch = await _patch_role(db_client, viewer, workspace["id"], viewer_member, "ADMIN")
    delete = await _delete_member(db_client, viewer, workspace["id"], viewer_member)

    assert patch.status_code == delete.status_code == 403


async def test_member_endpoints_require_authentication(db_client: AsyncClient) -> None:
    response = await db_client.get(f"{WORKSPACES}/{uuid.uuid4()}/members")

    assert response.status_code == 401


async def test_a_member_id_from_another_workspace_is_not_found(db_client: AsyncClient) -> None:
    ada = await _signup(db_client, "ada@example.com")
    bob = await _signup(db_client, "bob@example.com")
    carol = await _signup(db_client, "carol@example.com")
    workspace_a = await _create_workspace(db_client, ada, name="A")
    workspace_b = await _create_workspace(db_client, bob, name="B")
    carol_in_b = await _added_member_id(db_client, bob, workspace_b["id"], carol.email, "VIEWER")

    patch = await _patch_role(db_client, ada, workspace_a["id"], carol_in_b, "ADMIN")
    delete = await _delete_member(db_client, ada, workspace_a["id"], carol_in_b)

    assert patch.status_code == delete.status_code == 404
    assert _error(patch) == _error(delete) == ("NOT_FOUND", "Member not found.")
    members_b = await db_client.get(
        f"{WORKSPACES}/{workspace_b['id']}/members", headers=bob.headers
    )
    assert (carol.id, "VIEWER") in [(m["user_id"], m["role"]) for m in members_b.json()["items"]]


async def test_an_unknown_member_id_is_not_found(db_client: AsyncClient) -> None:
    ada = await _signup(db_client, "ada@example.com")
    workspace = await _create_workspace(db_client, ada)

    response = await _patch_role(db_client, ada, workspace["id"], str(uuid.uuid4()), "VIEWER")

    assert response.status_code == 404
    assert _error(response) == ("NOT_FOUND", "Member not found.")


# Members: add


async def test_an_admin_adds_an_existing_user_by_email(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await _signup(db_client, "admin@example.com")
    bob = await _signup(db_client, "bob@example.com", full_name="Bob Builder")
    workspace = await _create_workspace(db_client, admin)

    response = await _add_member(db_client, admin, workspace["id"], "BOB@Example.com", "ENGINEER")

    assert response.status_code == 201
    body = response.json()
    assert (body["user_id"], body["email"], body["full_name"], body["role"]) == (
        bob.id,
        "bob@example.com",
        "Bob Builder",
        "ENGINEER",
    )
    [audit] = await _audits(db_session, "MEMBER_ADDED")
    assert audit.actor_id == uuid.UUID(admin.id)
    assert audit.workspace_id == uuid.UUID(workspace["id"])
    assert (audit.entity_type, audit.entity_id) == ("workspace_member", uuid.UUID(body["id"]))
    assert audit.action_metadata == {"target_user_id": bob.id, "role": "ENGINEER"}


async def test_adding_an_unknown_email_is_user_not_found(db_client: AsyncClient) -> None:
    admin = await _signup(db_client, "admin@example.com")
    workspace = await _create_workspace(db_client, admin)

    response = await _add_member(db_client, admin, workspace["id"], "nobody@example.com", "VIEWER")

    assert response.status_code == 404
    assert _error(response) == ("USER_NOT_FOUND", "No account exists with that email.")


async def test_adding_an_inactive_user_is_user_not_found(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await _signup(db_client, "admin@example.com")
    dormant = await _signup(db_client, "dormant@example.com")
    workspace = await _create_workspace(db_client, admin)
    await db_session.execute(
        update(User).where(User.id == uuid.UUID(dormant.id)).values(is_active=False)
    )
    await db_session.commit()

    response = await _add_member(db_client, admin, workspace["id"], dormant.email, "VIEWER")

    assert response.status_code == 404
    assert _error(response)[0] == "USER_NOT_FOUND"


async def test_adding_an_existing_member_is_a_conflict(db_client: AsyncClient) -> None:
    admin = await _signup(db_client, "admin@example.com")
    bob = await _signup(db_client, "bob@example.com")
    workspace = await _create_workspace(db_client, admin)
    await _add_member(db_client, admin, workspace["id"], bob.email, "VIEWER")

    response = await _add_member(db_client, admin, workspace["id"], bob.email, "ADMIN")

    assert response.status_code == 409
    assert _error(response)[0] == "ALREADY_MEMBER"


async def test_adding_members_is_rate_limited_per_acting_user(
    limited_client: AsyncClient,
) -> None:
    admin = await _signup(limited_client, "admin@example.com")
    other_admin = await _signup(limited_client, "other@example.com")
    workspace = await _create_workspace(limited_client, admin)
    other_workspace = await _create_workspace(limited_client, other_admin, name="Other Works")
    for attempt in range(30):
        response = await _add_member(
            limited_client, admin, workspace["id"], f"nobody{attempt}@example.com", "VIEWER"
        )
        assert response.status_code == 404

    limited = await _add_member(
        limited_client, admin, workspace["id"], "other@example.com", "VIEWER"
    )

    assert limited.status_code == 429
    assert _error(limited)[0] == "RATE_LIMITED"
    assert 0 < int(limited.headers["retry-after"]) <= 3600
    unaffected = await _add_member(
        limited_client, other_admin, other_workspace["id"], "admin@example.com", "VIEWER"
    )
    assert unaffected.status_code == 201


@pytest.mark.parametrize(
    "body",
    [
        {"email": "not-an-email", "role": "VIEWER"},
        {"email": "bob@example.com", "role": "OWNER"},
        {"email": "bob@example.com"},
        {"email": "bob@example.com", "role": "VIEWER", "workspace_id": str(uuid.uuid4())},
    ],
    ids=["bad-email", "unknown-role", "missing-role", "unknown-field"],
)
async def test_add_member_rejects_an_invalid_body(
    db_client: AsyncClient, body: dict[str, str]
) -> None:
    admin = await _signup(db_client, "admin@example.com")
    workspace = await _create_workspace(db_client, admin)

    response = await db_client.post(
        f"{WORKSPACES}/{workspace['id']}/members", json=body, headers=admin.headers
    )

    assert response.status_code == 422


# Members: change role


async def test_an_admin_changes_a_members_role_and_it_is_audited(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await _signup(db_client, "admin@example.com")
    bob = await _signup(db_client, "bob@example.com")
    workspace = await _create_workspace(db_client, admin)
    bob_member = await _added_member_id(db_client, admin, workspace["id"], bob.email, "VIEWER")

    response = await _patch_role(db_client, admin, workspace["id"], bob_member, "ENGINEER")

    assert response.status_code == 200
    assert (response.json()["id"], response.json()["role"]) == (bob_member, "ENGINEER")
    [audit] = await _audits(db_session, "MEMBER_ROLE_CHANGED")
    assert audit.actor_id == uuid.UUID(admin.id)
    assert audit.workspace_id == uuid.UUID(workspace["id"])
    assert audit.entity_id == uuid.UUID(bob_member)
    assert audit.action_metadata == {"target_user_id": bob.id, "from": "VIEWER", "to": "ENGINEER"}


async def test_setting_the_same_role_is_a_no_op_without_an_audit(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await _signup(db_client, "admin@example.com")
    bob = await _signup(db_client, "bob@example.com")
    workspace = await _create_workspace(db_client, admin)
    bob_member = await _added_member_id(db_client, admin, workspace["id"], bob.email, "VIEWER")

    response = await _patch_role(db_client, admin, workspace["id"], bob_member, "VIEWER")

    assert response.status_code == 200
    assert response.json()["role"] == "VIEWER"
    assert await _audits(db_session, "MEMBER_ROLE_CHANGED") == []


async def test_the_new_role_takes_effect_on_the_next_request(db_client: AsyncClient) -> None:
    admin = await _signup(db_client, "admin@example.com")
    bob = await _signup(db_client, "bob@example.com")
    await _signup(db_client, "carol@example.com")
    workspace = await _create_workspace(db_client, admin)
    bob_member = await _added_member_id(db_client, admin, workspace["id"], bob.email, "ADMIN")
    await _patch_role(db_client, admin, workspace["id"], bob_member, "VIEWER")

    response = await _add_member(db_client, bob, workspace["id"], "carol@example.com", "VIEWER")

    assert response.status_code == 403


# LAST_ADMIN


async def test_the_last_admin_cannot_be_demoted(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await _signup(db_client, "admin@example.com")
    workspace = await _create_workspace(db_client, admin)
    admin_member = await _member_id_of(db_client, admin, workspace["id"], admin.id)

    response = await _patch_role(db_client, admin, workspace["id"], admin_member, "ENGINEER")

    assert response.status_code == 409
    assert _error(response) == ("LAST_ADMIN", "A workspace must keep at least one admin.")
    assert await _admin_count(db_session, workspace["id"]) == 1


async def test_the_last_admin_cannot_be_removed(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await _signup(db_client, "admin@example.com")
    workspace = await _create_workspace(db_client, admin)
    admin_member = await _member_id_of(db_client, admin, workspace["id"], admin.id)

    response = await _delete_member(db_client, admin, workspace["id"], admin_member)

    assert response.status_code == 409
    assert _error(response)[0] == "LAST_ADMIN"
    assert await _admin_count(db_session, workspace["id"]) == 1
    assert await _audits(db_session, "MEMBER_REMOVED") == []


async def test_an_admin_can_be_demoted_while_another_admin_remains(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await _signup(db_client, "admin@example.com")
    bob = await _signup(db_client, "bob@example.com")
    workspace = await _create_workspace(db_client, admin)
    bob_member = await _added_member_id(db_client, admin, workspace["id"], bob.email, "ADMIN")

    response = await _patch_role(db_client, admin, workspace["id"], bob_member, "VIEWER")

    assert response.status_code == 200
    assert await _admin_count(db_session, workspace["id"]) == 1


async def test_an_admin_may_remove_themselves_while_another_admin_remains(
    db_client: AsyncClient,
) -> None:
    admin = await _signup(db_client, "admin@example.com")
    bob = await _signup(db_client, "bob@example.com")
    workspace = await _create_workspace(db_client, admin)
    await _add_member(db_client, admin, workspace["id"], bob.email, "ADMIN")
    admin_member = await _member_id_of(db_client, admin, workspace["id"], admin.id)

    response = await _delete_member(db_client, admin, workspace["id"], admin_member)

    assert response.status_code == 204
    after = await db_client.get(f"{WORKSPACES}/{workspace['id']}", headers=admin.headers)
    assert after.status_code == 404


async def test_two_admins_demoting_each_other_concurrently_leave_one_admin(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    ada = await _signup(db_client, "ada@example.com")
    bob = await _signup(db_client, "bob@example.com")
    workspace = await _create_workspace(db_client, ada)
    bob_member = await _added_member_id(db_client, ada, workspace["id"], bob.email, "ADMIN")
    ada_member = await _member_id_of(db_client, ada, workspace["id"], ada.id)

    responses = await asyncio.gather(
        _patch_role(db_client, ada, workspace["id"], bob_member, "VIEWER"),
        _patch_role(db_client, bob, workspace["id"], ada_member, "VIEWER"),
    )

    statuses = sorted(response.status_code for response in responses)
    # The loser either waited on the ADMIN-row lock and hit LAST_ADMIN, or ran after the winner
    # committed and was no longer an admin.
    assert statuses[0] == 200
    assert statuses[1] in {403, 409}
    assert await _admin_count(db_session, workspace["id"]) == 1


# Members: remove (amendment B)


async def test_removal_is_audited_with_actor_workspace_target_and_previous_role(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await _signup(db_client, "admin@example.com")
    bob = await _signup(db_client, "bob@example.com")
    workspace = await _create_workspace(db_client, admin)
    bob_member = await _added_member_id(db_client, admin, workspace["id"], bob.email, "ENGINEER")

    response = await _delete_member(db_client, admin, workspace["id"], bob_member)

    assert response.status_code == 204
    assert response.content == b""
    [audit] = await _audits(db_session, "MEMBER_REMOVED")
    assert audit.actor_id == uuid.UUID(admin.id)
    assert audit.workspace_id == uuid.UUID(workspace["id"])
    assert (audit.entity_type, audit.entity_id) == ("workspace_member", uuid.UUID(bob_member))
    assert audit.action_metadata == {"target_user_id": bob.id, "previous_role": "ENGINEER"}
    assert audit.created_at is not None


async def test_removal_keeps_the_user_and_the_audit_rows_they_authored(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await _signup(db_client, "admin@example.com")
    bob = await _signup(db_client, "bob@example.com")
    await _signup(db_client, "carol@example.com")
    workspace = await _create_workspace(db_client, admin)
    bob_member = await _added_member_id(db_client, admin, workspace["id"], bob.email, "ADMIN")
    await _add_member(db_client, bob, workspace["id"], "carol@example.com", "VIEWER")
    bob_uuid = uuid.UUID(bob.id)
    db_session.expire_all()
    authored_before = (
        await db_session.scalars(select(AuditLog.id).where(AuditLog.actor_id == bob_uuid))
    ).all()

    await _delete_member(db_client, admin, workspace["id"], bob_member)

    db_session.expire_all()
    assert await db_session.get(User, bob_uuid) is not None
    authored_after = (
        await db_session.scalars(select(AuditLog.id).where(AuditLog.actor_id == bob_uuid))
    ).all()
    assert len(authored_before) >= 3  # registered, logged in, added carol
    assert sorted(authored_after) == sorted(authored_before)
    assert await db_session.get(WorkspaceMember, uuid.UUID(bob_member)) is None


async def test_a_removed_user_loses_access_and_can_be_re_added(db_client: AsyncClient) -> None:
    admin = await _signup(db_client, "admin@example.com")
    bob = await _signup(db_client, "bob@example.com")
    workspace = await _create_workspace(db_client, admin)
    bob_member = await _added_member_id(db_client, admin, workspace["id"], bob.email, "VIEWER")
    await _delete_member(db_client, admin, workspace["id"], bob_member)

    lost = await db_client.get(f"{WORKSPACES}/{workspace['id']}", headers=bob.headers)
    re_added = await _add_member(db_client, admin, workspace["id"], bob.email, "ENGINEER")
    regained = await db_client.get(f"{WORKSPACES}/{workspace['id']}", headers=bob.headers)

    assert lost.status_code == 404
    assert _error(lost) == ("NOT_FOUND", "Workspace not found.")
    assert re_added.status_code == 201
    assert re_added.json()["id"] != bob_member
    assert regained.status_code == 200
    assert regained.json()["role"] == "ENGINEER"


# OpenAPI


async def test_openapi_documents_the_workspace_endpoints(db_client: AsyncClient) -> None:
    document = (await db_client.get(f"{API}/openapi.json")).json()
    paths = document["paths"]

    assert {path for path in paths if path.startswith(WORKSPACES)} == {
        WORKSPACES,
        f"{WORKSPACES}/{{workspace_id}}",
        f"{WORKSPACES}/{{workspace_id}}/members",
        f"{WORKSPACES}/{{workspace_id}}/members/{{member_id}}",
        # Owned by the products router; asserted in detail in test_products_api.py.
        f"{WORKSPACES}/{{workspace_id}}/products",
    }
    assert set(paths[WORKSPACES]) == {"get", "post"}
    assert set(paths[f"{WORKSPACES}/{{workspace_id}}/members"]) == {"get", "post"}
    assert set(paths[f"{WORKSPACES}/{{workspace_id}}/members/{{member_id}}"]) == {
        "patch",
        "delete",
    }
    error_ref = "#/components/schemas/ErrorResponse"
    add_member = paths[f"{WORKSPACES}/{{workspace_id}}/members"]["post"]["responses"]
    assert {"201", "401", "403", "404", "409", "422", "429"} <= set(add_member)
    assert add_member["409"]["content"]["application/json"]["schema"]["$ref"] == error_ref
