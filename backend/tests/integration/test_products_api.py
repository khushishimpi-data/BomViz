"""Products and revisions over HTTP against a real PostgreSQL.

Covers creation, unique codes, listing with search/status filters and pagination, product detail
with revisions newest first, role permissions (403), cross-tenant isolation (404) and the audit
rows written for each creation.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models import AuditLog, ProductRevision
from tests.conftest import make_settings

pytestmark = pytest.mark.integration

SECRET = "test-secret-key-that-is-long-enough-for-hs256-0123456789"
PASSWORD = "a very long passphrase"
API = "/api/v1"
WORKSPACES = f"{API}/workspaces"
PRODUCTS = f"{API}/products"


@pytest.fixture
def db_settings(migrated_database_url: str, truncate_tables: None) -> Settings:
    return make_settings(
        database_url=migrated_database_url, jwt_secret_key=SECRET, rate_limit_enabled=False
    )


# Helpers


@dataclass(frozen=True)
class Actor:
    id: str
    email: str
    headers: dict[str, str]


async def _signup(client: AsyncClient, email: str) -> Actor:
    registered = await client.post(
        f"{API}/auth/register",
        json={"email": email, "password": PASSWORD, "full_name": "Test User"},
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


async def _create_workspace(client: AsyncClient, actor: Actor, name: str = "Acme") -> str:
    response = await client.post(WORKSPACES, json={"name": name}, headers=actor.headers)
    assert response.status_code == 201, response.text
    workspace_id: str = response.json()["id"]
    return workspace_id


async def _member(client: AsyncClient, admin: Actor, workspace_id: str, role: str) -> Actor:
    """Sign up a new user and add them to `workspace_id` with `role`."""
    actor = await _signup(client, f"{role.lower()}-{uuid.uuid4().hex[:8]}@example.com")
    response = await client.post(
        f"{WORKSPACES}/{workspace_id}/members",
        json={"email": actor.email, "role": role},
        headers=admin.headers,
    )
    assert response.status_code == 201, response.text
    return actor


def _products_url(workspace_id: str) -> str:
    return f"{WORKSPACES}/{workspace_id}/products"


async def _post_product(
    client: AsyncClient, actor: Actor, workspace_id: str, **body: Any
) -> Response:
    payload = {"product_code": "PRD-001", "name": "Widget", **body}
    return await client.post(_products_url(workspace_id), json=payload, headers=actor.headers)


async def _product(client: AsyncClient, actor: Actor, workspace_id: str, **body: Any) -> str:
    response = await _post_product(client, actor, workspace_id, **body)
    assert response.status_code == 201, response.text
    product_id: str = response.json()["id"]
    return product_id


async def _post_revision(
    client: AsyncClient, actor: Actor, product_id: str, **body: Any
) -> Response:
    payload = {"revision_code": "A", **body}
    return await client.post(
        f"{PRODUCTS}/{product_id}/revisions", json=payload, headers=actor.headers
    )


async def _list(client: AsyncClient, actor: Actor, workspace_id: str, query: str = "") -> Response:
    return await client.get(f"{_products_url(workspace_id)}{query}", headers=actor.headers)


def _codes(response: Response) -> list[str]:
    assert response.status_code == 200, response.text
    return [item["product_code"] for item in response.json()["items"]]


def _error(response: Response) -> tuple[str, str]:
    error = response.json()["error"]
    return error["code"], error["message"]


async def _audits(session: AsyncSession, action: str) -> list[AuditLog]:
    session.expire_all()
    result = await session.execute(
        select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.created_at)
    )
    return list(result.scalars().all())


# Create


async def test_an_engineer_creates_a_product(db_client: AsyncClient) -> None:
    admin = await _signup(db_client, "admin@example.com")
    workspace_id = await _create_workspace(db_client, admin)
    engineer = await _member(db_client, admin, workspace_id, "ENGINEER")

    response = await _post_product(
        db_client,
        engineer,
        workspace_id,
        product_code="  PRD-001  ",
        name="  Widget  ",
        description="  A small widget  ",
    )

    assert response.status_code == 201
    body = response.json()
    assert set(body) == {
        "id",
        "workspace_id",
        "product_code",
        "name",
        "description",
        "status",
        "created_by_id",
        "created_at",
        "updated_at",
    }
    assert body["workspace_id"] == workspace_id
    assert (body["product_code"], body["name"]) == ("PRD-001", "Widget")
    assert body["description"] == "A small widget"
    assert body["status"] == "DRAFT"
    assert body["created_by_id"] == engineer.id


async def test_create_accepts_an_explicit_status_and_no_description(
    db_client: AsyncClient,
) -> None:
    admin = await _signup(db_client, "admin@example.com")
    workspace_id = await _create_workspace(db_client, admin)

    response = await _post_product(db_client, admin, workspace_id, status="ACTIVE")

    assert response.status_code == 201
    assert response.json()["status"] == "ACTIVE"
    assert response.json()["description"] is None


async def test_a_blank_description_is_stored_as_none(db_client: AsyncClient) -> None:
    admin = await _signup(db_client, "admin@example.com")
    workspace_id = await _create_workspace(db_client, admin)

    response = await _post_product(db_client, admin, workspace_id, description="   ")

    assert response.status_code == 201
    assert response.json()["description"] is None


async def test_create_accepts_fields_at_their_length_limits(db_client: AsyncClient) -> None:
    admin = await _signup(db_client, "admin@example.com")
    workspace_id = await _create_workspace(db_client, admin)

    response = await _post_product(
        db_client,
        admin,
        workspace_id,
        product_code="C" * 64,
        name="N" * 200,
        description="D" * 2000,
    )

    assert response.status_code == 201


async def test_product_creation_is_audited(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await _signup(db_client, "admin@example.com")
    workspace_id = await _create_workspace(db_client, admin)
    product_id = await _product(db_client, admin, workspace_id, product_code="PRD-9")

    [audit] = await _audits(db_session, "PRODUCT_CREATED")

    assert audit.actor_id == uuid.UUID(admin.id)
    assert audit.workspace_id == uuid.UUID(workspace_id)
    assert (audit.entity_type, audit.entity_id) == ("product", uuid.UUID(product_id))
    assert audit.action_metadata == {"product_code": "PRD-9"}


async def test_a_duplicate_product_code_is_a_conflict(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await _signup(db_client, "admin@example.com")
    workspace_id = await _create_workspace(db_client, admin)
    await _product(db_client, admin, workspace_id, product_code="PRD-1")

    response = await _post_product(db_client, admin, workspace_id, product_code="PRD-1")

    assert response.status_code == 409
    assert _error(response)[0] == "PRODUCT_CODE_TAKEN"
    assert len(await _audits(db_session, "PRODUCT_CREATED")) == 1


async def test_the_same_product_code_may_exist_in_another_workspace(
    db_client: AsyncClient,
) -> None:
    admin = await _signup(db_client, "admin@example.com")
    alpha = await _create_workspace(db_client, admin, name="Alpha")
    beta = await _create_workspace(db_client, admin, name="Beta")
    await _product(db_client, admin, alpha, product_code="PRD-1")

    response = await _post_product(db_client, admin, beta, product_code="PRD-1")

    assert response.status_code == 201


@pytest.mark.parametrize(
    "body",
    [
        {"product_code": "", "name": "Widget"},
        {"product_code": "   ", "name": "Widget"},
        {"product_code": "C" * 65, "name": "Widget"},
        {"product_code": "PRD-1", "name": ""},
        {"product_code": "PRD-1", "name": "N" * 201},
        {"product_code": "PRD-1", "name": "Widget", "description": "D" * 2001},
        {"product_code": "PRD-1", "name": "Widget", "status": "RELEASED"},
        {"product_code": "PRD-1", "name": "Widget", "workspace_id": str(uuid.uuid4())},
        {"product_code": "PRD-1", "name": "Widget", "created_by_id": str(uuid.uuid4())},
        {"name": "Widget"},
    ],
    ids=[
        "empty-code",
        "blank-code",
        "long-code",
        "empty-name",
        "long-name",
        "long-description",
        "unknown-status",
        "unknown-field-workspace",
        "unknown-field-creator",
        "missing-code",
    ],
)
async def test_create_rejects_an_invalid_body(db_client: AsyncClient, body: dict[str, str]) -> None:
    admin = await _signup(db_client, "admin@example.com")
    workspace_id = await _create_workspace(db_client, admin)

    response = await db_client.post(_products_url(workspace_id), json=body, headers=admin.headers)

    assert response.status_code == 422
    assert _error(response)[0] == "VALIDATION_ERROR"


# Permissions and isolation


@pytest.mark.parametrize("role", ["VIEWER", "PRODUCTION", "PROCUREMENT"])
async def test_read_only_roles_cannot_create_products(db_client: AsyncClient, role: str) -> None:
    admin = await _signup(db_client, "admin@example.com")
    workspace_id = await _create_workspace(db_client, admin)
    member = await _member(db_client, admin, workspace_id, role)

    response = await _post_product(db_client, member, workspace_id)

    assert response.status_code == 403
    assert _error(response)[0] == "FORBIDDEN"


@pytest.mark.parametrize("role", ["VIEWER", "PRODUCTION", "PROCUREMENT"])
async def test_read_only_roles_can_list_and_read_products(
    db_client: AsyncClient, role: str
) -> None:
    admin = await _signup(db_client, "admin@example.com")
    workspace_id = await _create_workspace(db_client, admin)
    product_id = await _product(db_client, admin, workspace_id)
    member = await _member(db_client, admin, workspace_id, role)

    listed = await _list(db_client, member, workspace_id)
    detail = await db_client.get(f"{PRODUCTS}/{product_id}", headers=member.headers)

    assert _codes(listed) == ["PRD-001"]
    assert detail.status_code == 200
    assert detail.json()["id"] == product_id


@pytest.mark.parametrize("role", ["VIEWER", "PRODUCTION", "PROCUREMENT"])
async def test_read_only_roles_cannot_create_revisions(db_client: AsyncClient, role: str) -> None:
    admin = await _signup(db_client, "admin@example.com")
    workspace_id = await _create_workspace(db_client, admin)
    product_id = await _product(db_client, admin, workspace_id)
    member = await _member(db_client, admin, workspace_id, role)

    response = await _post_revision(db_client, member, product_id)

    assert response.status_code == 403
    assert _error(response)[0] == "FORBIDDEN"


async def test_a_non_member_gets_404_for_the_workspace_product_endpoints(
    db_client: AsyncClient,
) -> None:
    admin = await _signup(db_client, "admin@example.com")
    outsider = await _signup(db_client, "outsider@example.com")
    workspace_id = await _create_workspace(db_client, admin)

    listed = await _list(db_client, outsider, workspace_id)
    created = await _post_product(db_client, outsider, workspace_id)

    assert listed.status_code == 404
    assert created.status_code == 404
    assert _error(listed) == ("NOT_FOUND", "Workspace not found.")
    assert _error(created) == ("NOT_FOUND", "Workspace not found.")


async def test_a_non_member_gets_404_on_product_detail_and_revision_create(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await _signup(db_client, "admin@example.com")
    outsider = await _signup(db_client, "outsider@example.com")
    workspace_id = await _create_workspace(db_client, admin)
    product_id = await _product(db_client, admin, workspace_id)

    detail = await db_client.get(f"{PRODUCTS}/{product_id}", headers=outsider.headers)
    revision = await _post_revision(db_client, outsider, product_id)

    assert detail.status_code == 404
    assert revision.status_code == 404
    assert _error(detail) == ("NOT_FOUND", "Product not found.")
    assert _error(revision) == ("NOT_FOUND", "Product not found.")
    assert await _audits(db_session, "PRODUCT_REVISION_CREATED") == []


async def test_a_member_of_another_workspace_gets_404_on_its_products(
    db_client: AsyncClient,
) -> None:
    alice = await _signup(db_client, "alice@example.com")
    bob = await _signup(db_client, "bob@example.com")
    await _create_workspace(db_client, alice, name="Alpha")
    beta = await _create_workspace(db_client, bob, name="Beta")
    beta_product = await _product(db_client, bob, beta)

    detail = await db_client.get(f"{PRODUCTS}/{beta_product}", headers=alice.headers)
    revision = await _post_revision(db_client, alice, beta_product)

    assert _error(detail) == ("NOT_FOUND", "Product not found.")
    assert _error(revision) == ("NOT_FOUND", "Product not found.")


async def test_an_unknown_product_id_is_not_found(db_client: AsyncClient) -> None:
    admin = await _signup(db_client, "admin@example.com")

    response = await db_client.get(f"{PRODUCTS}/{uuid.uuid4()}", headers=admin.headers)

    assert response.status_code == 404
    assert _error(response) == ("NOT_FOUND", "Product not found.")


async def test_a_malformed_product_id_is_a_validation_error(db_client: AsyncClient) -> None:
    admin = await _signup(db_client, "admin@example.com")

    response = await db_client.get(f"{PRODUCTS}/not-a-uuid", headers=admin.headers)

    assert response.status_code == 422
    assert _error(response)[0] == "VALIDATION_ERROR"


async def test_product_endpoints_require_authentication(db_client: AsyncClient) -> None:
    admin = await _signup(db_client, "admin@example.com")
    workspace_id = await _create_workspace(db_client, admin)
    product_id = await _product(db_client, admin, workspace_id)

    responses = [
        await db_client.get(_products_url(workspace_id)),
        await db_client.post(_products_url(workspace_id), json={"product_code": "X", "name": "X"}),
        await db_client.get(f"{PRODUCTS}/{product_id}"),
        await db_client.post(f"{PRODUCTS}/{product_id}/revisions", json={"revision_code": "B"}),
    ]

    assert [r.status_code for r in responses] == [401, 401, 401, 401]
    assert {_error(r)[0] for r in responses} == {"UNAUTHENTICATED"}


# List


async def test_list_is_ordered_by_product_code_with_the_page_envelope(
    db_client: AsyncClient,
) -> None:
    admin = await _signup(db_client, "admin@example.com")
    workspace_id = await _create_workspace(db_client, admin)
    for code in ["PRD-C", "PRD-A", "PRD-B"]:
        await _product(db_client, admin, workspace_id, product_code=code)

    response = await _list(db_client, admin, workspace_id)

    assert _codes(response) == ["PRD-A", "PRD-B", "PRD-C"]
    body = response.json()
    assert (body["total"], body["limit"], body["offset"]) == (3, 50, 0)


async def test_list_contains_only_the_workspaces_own_products(db_client: AsyncClient) -> None:
    admin = await _signup(db_client, "admin@example.com")
    alpha = await _create_workspace(db_client, admin, name="Alpha")
    beta = await _create_workspace(db_client, admin, name="Beta")
    await _product(db_client, admin, alpha, product_code="ALPHA-1")
    await _product(db_client, admin, beta, product_code="BETA-1")

    response = await _list(db_client, admin, alpha)

    assert _codes(response) == ["ALPHA-1"]


async def test_list_paginates_with_the_full_total(db_client: AsyncClient) -> None:
    admin = await _signup(db_client, "admin@example.com")
    workspace_id = await _create_workspace(db_client, admin)
    for code in ["P1", "P2", "P3", "P4"]:
        await _product(db_client, admin, workspace_id, product_code=code)

    response = await _list(db_client, admin, workspace_id, "?limit=2&offset=1")

    assert _codes(response) == ["P2", "P3"]
    body = response.json()
    assert (body["total"], body["limit"], body["offset"]) == (4, 2, 1)


async def test_q_matches_code_or_name_case_insensitively(db_client: AsyncClient) -> None:
    admin = await _signup(db_client, "admin@example.com")
    workspace_id = await _create_workspace(db_client, admin)
    await _product(db_client, admin, workspace_id, product_code="MTR-1", name="Motor")
    await _product(db_client, admin, workspace_id, product_code="GBX-1", name="Gearbox motor")
    await _product(db_client, admin, workspace_id, product_code="FRM-1", name="Frame")

    by_name = await _list(db_client, admin, workspace_id, "?q=MOTOR")
    by_code = await _list(db_client, admin, workspace_id, "?q=frm")

    assert _codes(by_name) == ["GBX-1", "MTR-1"]
    assert by_name.json()["total"] == 2
    assert _codes(by_code) == ["FRM-1"]


async def test_q_matches_a_percent_sign_literally(db_client: AsyncClient) -> None:
    admin = await _signup(db_client, "admin@example.com")
    workspace_id = await _create_workspace(db_client, admin)
    await _product(db_client, admin, workspace_id, product_code="P1", name="100% cotton")
    await _product(db_client, admin, workspace_id, product_code="P2", name="100 cotton")

    response = await _list(db_client, admin, workspace_id, "?q=100%25")

    assert _codes(response) == ["P1"]


async def test_q_matches_an_underscore_literally(db_client: AsyncClient) -> None:
    admin = await _signup(db_client, "admin@example.com")
    workspace_id = await _create_workspace(db_client, admin)
    await _product(db_client, admin, workspace_id, product_code="A_B", name="Underscored")
    await _product(db_client, admin, workspace_id, product_code="AXB", name="Plain")

    response = await _list(db_client, admin, workspace_id, "?q=a_b")

    assert _codes(response) == ["A_B"]


async def test_q_matches_a_backslash_literally(db_client: AsyncClient) -> None:
    admin = await _signup(db_client, "admin@example.com")
    workspace_id = await _create_workspace(db_client, admin)
    await _product(db_client, admin, workspace_id, product_code="P1", name="C:\\parts")
    await _product(db_client, admin, workspace_id, product_code="P2", name="C:parts")

    response = await _list(db_client, admin, workspace_id, "?q=%5C")

    assert _codes(response) == ["P1"]


async def test_a_blank_q_does_not_filter(db_client: AsyncClient) -> None:
    admin = await _signup(db_client, "admin@example.com")
    workspace_id = await _create_workspace(db_client, admin)
    await _product(db_client, admin, workspace_id, product_code="P1")

    response = await _list(db_client, admin, workspace_id, "?q=%20%20")

    assert _codes(response) == ["P1"]


async def test_status_filters_the_list_and_the_total(db_client: AsyncClient) -> None:
    admin = await _signup(db_client, "admin@example.com")
    workspace_id = await _create_workspace(db_client, admin)
    await _product(db_client, admin, workspace_id, product_code="P1", status="ACTIVE")
    await _product(db_client, admin, workspace_id, product_code="P2")
    await _product(db_client, admin, workspace_id, product_code="P3", status="ACTIVE")

    response = await _list(db_client, admin, workspace_id, "?status=ACTIVE")

    assert _codes(response) == ["P1", "P3"]
    assert response.json()["total"] == 2


async def test_q_and_status_combine(db_client: AsyncClient) -> None:
    admin = await _signup(db_client, "admin@example.com")
    workspace_id = await _create_workspace(db_client, admin)
    await _product(db_client, admin, workspace_id, product_code="MTR-1", status="ACTIVE")
    await _product(db_client, admin, workspace_id, product_code="MTR-2")
    await _product(db_client, admin, workspace_id, product_code="FRM-1", status="ACTIVE")

    response = await _list(db_client, admin, workspace_id, "?q=mtr&status=ACTIVE")

    assert _codes(response) == ["MTR-1"]


@pytest.mark.parametrize(
    "query",
    ["?status=RELEASED", "?limit=0", "?limit=201", "?offset=-1", f"?q={'x' * 201}"],
    ids=["unknown-status", "limit-zero", "limit-too-big", "negative-offset", "long-q"],
)
async def test_list_rejects_invalid_query_parameters(db_client: AsyncClient, query: str) -> None:
    admin = await _signup(db_client, "admin@example.com")
    workspace_id = await _create_workspace(db_client, admin)

    response = await _list(db_client, admin, workspace_id, query)

    assert response.status_code == 422
    assert _error(response)[0] == "VALIDATION_ERROR"


# Detail and revisions


async def test_detail_returns_the_product_with_an_empty_revision_list(
    db_client: AsyncClient,
) -> None:
    admin = await _signup(db_client, "admin@example.com")
    workspace_id = await _create_workspace(db_client, admin)
    created = (await _post_product(db_client, admin, workspace_id)).json()

    response = await db_client.get(f"{PRODUCTS}/{created['id']}", headers=admin.headers)

    assert response.status_code == 200
    body = response.json()
    assert body.pop("revisions") == []
    assert body == created


async def test_an_engineer_creates_a_revision(db_client: AsyncClient) -> None:
    admin = await _signup(db_client, "admin@example.com")
    workspace_id = await _create_workspace(db_client, admin)
    product_id = await _product(db_client, admin, workspace_id)
    engineer = await _member(db_client, admin, workspace_id, "ENGINEER")

    response = await _post_revision(
        db_client, engineer, product_id, revision_code="  A  ", description="First cut"
    )

    assert response.status_code == 201
    body = response.json()
    assert set(body) == {
        "id",
        "product_id",
        "revision_code",
        "description",
        "status",
        "created_by_id",
        "created_at",
    }
    assert body["product_id"] == product_id
    assert body["revision_code"] == "A"
    assert body["description"] == "First cut"
    assert body["status"] == "DRAFT"
    assert body["created_by_id"] == engineer.id


async def test_revision_creation_is_audited(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await _signup(db_client, "admin@example.com")
    workspace_id = await _create_workspace(db_client, admin)
    product_id = await _product(db_client, admin, workspace_id)
    revision_id = (await _post_revision(db_client, admin, product_id, revision_code="B")).json()[
        "id"
    ]

    [audit] = await _audits(db_session, "PRODUCT_REVISION_CREATED")

    assert audit.actor_id == uuid.UUID(admin.id)
    assert audit.workspace_id == uuid.UUID(workspace_id)
    assert (audit.entity_type, audit.entity_id) == ("product_revision", uuid.UUID(revision_id))
    assert audit.action_metadata == {"revision_code": "B"}


async def test_a_duplicate_revision_code_is_a_conflict(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await _signup(db_client, "admin@example.com")
    workspace_id = await _create_workspace(db_client, admin)
    product_id = await _product(db_client, admin, workspace_id)
    await _post_revision(db_client, admin, product_id, revision_code="A")

    response = await _post_revision(db_client, admin, product_id, revision_code="A")

    assert response.status_code == 409
    assert _error(response)[0] == "REVISION_CODE_TAKEN"
    assert len(await _audits(db_session, "PRODUCT_REVISION_CREATED")) == 1


async def test_the_same_revision_code_may_exist_on_another_product(
    db_client: AsyncClient,
) -> None:
    admin = await _signup(db_client, "admin@example.com")
    workspace_id = await _create_workspace(db_client, admin)
    first = await _product(db_client, admin, workspace_id, product_code="P1")
    second = await _product(db_client, admin, workspace_id, product_code="P2")
    await _post_revision(db_client, admin, first, revision_code="A")

    response = await _post_revision(db_client, admin, second, revision_code="A")

    assert response.status_code == 201


async def test_detail_lists_revisions_newest_first(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await _signup(db_client, "admin@example.com")
    workspace_id = await _create_workspace(db_client, admin)
    product_id = await _product(db_client, admin, workspace_id)
    ids = {}
    for code in ["A", "B", "C"]:
        ids[code] = (await _post_revision(db_client, admin, product_id, revision_code=code)).json()[
            "id"
        ]
    # Pin creation times so the order does not depend on insertion order or clock resolution.
    for code, day in [("A", 3), ("B", 1), ("C", 2)]:
        await db_session.execute(
            update(ProductRevision)
            .where(ProductRevision.id == uuid.UUID(ids[code]))
            .values(created_at=datetime(2026, 1, day, tzinfo=UTC))
        )
    await db_session.commit()

    response = await db_client.get(f"{PRODUCTS}/{product_id}", headers=admin.headers)

    assert [r["revision_code"] for r in response.json()["revisions"]] == ["A", "C", "B"]


async def test_detail_lists_only_the_products_own_revisions(db_client: AsyncClient) -> None:
    admin = await _signup(db_client, "admin@example.com")
    workspace_id = await _create_workspace(db_client, admin)
    first = await _product(db_client, admin, workspace_id, product_code="P1")
    second = await _product(db_client, admin, workspace_id, product_code="P2")
    await _post_revision(db_client, admin, first, revision_code="A")
    await _post_revision(db_client, admin, second, revision_code="Z")

    response = await db_client.get(f"{PRODUCTS}/{first}", headers=admin.headers)

    assert [r["revision_code"] for r in response.json()["revisions"]] == ["A"]


@pytest.mark.parametrize(
    "body",
    [
        {"revision_code": ""},
        {"revision_code": "   "},
        {"revision_code": "R" * 33},
        {"revision_code": "A", "description": "D" * 2001},
        {"revision_code": "A", "status": "ACTIVE"},
        {"revision_code": "A", "product_id": str(uuid.uuid4())},
        {},
    ],
    ids=[
        "empty-code",
        "blank-code",
        "long-code",
        "long-description",
        "unknown-status",
        "unknown-field",
        "missing-code",
    ],
)
async def test_revision_create_rejects_an_invalid_body(
    db_client: AsyncClient, body: dict[str, str]
) -> None:
    admin = await _signup(db_client, "admin@example.com")
    workspace_id = await _create_workspace(db_client, admin)
    product_id = await _product(db_client, admin, workspace_id)

    response = await db_client.post(
        f"{PRODUCTS}/{product_id}/revisions", json=body, headers=admin.headers
    )

    assert response.status_code == 422
    assert _error(response)[0] == "VALIDATION_ERROR"


async def test_revision_create_accepts_a_code_at_the_length_limit_and_a_status(
    db_client: AsyncClient,
) -> None:
    admin = await _signup(db_client, "admin@example.com")
    workspace_id = await _create_workspace(db_client, admin)
    product_id = await _product(db_client, admin, workspace_id)

    response = await _post_revision(
        db_client, admin, product_id, revision_code="R" * 32, status="RELEASED"
    )

    assert response.status_code == 201
    assert response.json()["status"] == "RELEASED"


# OpenAPI


async def test_openapi_documents_the_product_endpoints(db_client: AsyncClient) -> None:
    paths = (await db_client.get(f"{API}/openapi.json")).json()["paths"]

    products_list = f"{WORKSPACES}/{{workspace_id}}/products"
    assert set(paths[products_list]) == {"get", "post"}
    assert set(paths[f"{PRODUCTS}/{{product_id}}"]) == {"get"}
    assert set(paths[f"{PRODUCTS}/{{product_id}}/revisions"]) == {"post"}
    error_ref = "#/components/schemas/ErrorResponse"
    create = paths[products_list]["post"]["responses"]
    assert {"201", "401", "403", "404", "409", "422"} <= set(create)
    assert create["409"]["content"]["application/json"]["schema"]["$ref"] == error_ref
    revision = paths[f"{PRODUCTS}/{{product_id}}/revisions"]["post"]["responses"]
    assert {"201", "401", "403", "404", "409", "422"} <= set(revision)
