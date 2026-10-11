"""`AuditService.record` persists audit rows and refuses sensitive or non-JSON metadata.

The last two tests prove amendment B's history guarantee at the database level: audit rows
outlive their actor, and a workspace with audit history cannot be deleted.
"""

import uuid
from collections.abc import Iterator
from decimal import Decimal

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import request_id_ctx
from app.models import AuditLog, User, Workspace
from app.services.audit_service import AuditAction, AuditService

pytestmark = pytest.mark.integration

REQUEST_ID = "req-audit-0001"


@pytest.fixture
def request_id() -> Iterator[str]:
    token = request_id_ctx.set(REQUEST_ID)
    yield REQUEST_ID
    request_id_ctx.reset(token)


async def _add_user(session: AsyncSession, email: str) -> User:
    user = User(email=email, password_hash="not-a-real-hash", full_name="Audit Test")
    session.add(user)
    await session.flush()
    return user


async def _add_workspace(session: AsyncSession, owner: User) -> Workspace:
    workspace = Workspace(name="Audit Workspace", slug="audit-workspace", owner_id=owner.id)
    session.add(workspace)
    await session.flush()
    return workspace


async def _audit_row_count(session: AsyncSession) -> int:
    return await session.scalar(select(func.count(AuditLog.id))) or 0


async def test_record_persists_every_field(db_session: AsyncSession, request_id: str) -> None:
    actor = await _add_user(db_session, "actor@example.com")
    workspace = await _add_workspace(db_session, actor)
    member_id = uuid.uuid4()
    target_user_id = uuid.uuid4()

    entry = await AuditService(db_session).record(
        AuditAction.MEMBER_REMOVED,
        entity_type="workspace_member",
        entity_id=member_id,
        actor_id=actor.id,
        workspace_id=workspace.id,
        metadata={"target_user_id": str(target_user_id), "previous_role": "ENGINEER"},
    )
    await db_session.commit()
    db_session.expunge_all()

    stored = await db_session.get_one(AuditLog, entry.id)
    assert stored.action == "MEMBER_REMOVED"
    assert stored.entity_type == "workspace_member"
    assert stored.entity_id == member_id
    assert stored.actor_id == actor.id
    assert stored.workspace_id == workspace.id
    assert stored.action_metadata == {
        "target_user_id": str(target_user_id),
        "previous_role": "ENGINEER",
    }
    assert stored.request_id == request_id
    assert stored.created_at is not None


async def test_record_without_optional_fields_stores_nulls_and_empty_metadata(
    db_session: AsyncSession,
) -> None:
    entry = await AuditService(db_session).record(AuditAction.AUTH_LOGIN_FAILED, entity_type="user")
    await db_session.commit()
    db_session.expunge_all()

    stored = await db_session.get_one(AuditLog, entry.id)
    assert stored.actor_id is None
    assert stored.workspace_id is None
    assert stored.entity_id is None
    assert stored.action_metadata == {}
    assert stored.request_id is None


@pytest.mark.parametrize(
    "metadata",
    [
        {"password": "hunter2hunter2"},
        {"new_PASSWORD": "x"},
        {"refresh_token": "abc"},
        {"accessToken": "abc"},
        {"client_secret": "abc"},
        {"email": "person@example.com"},
        {"target_email": "person@example.com"},
        {"ip": "203.0.113.7"},
        {"client_ip": "203.0.113.7"},
        {"ipAddress": "203.0.113.7"},
        {"IP": "203.0.113.7"},
        {"key_hash": "deadbeef"},
        {"throttle_key_hash": "deadbeef"},
        {"details": {"email": "person@example.com"}},
        {"attempts": [{"ip": "203.0.113.7"}]},
    ],
)
async def test_record_rejects_sensitive_metadata_keys(
    db_session: AsyncSession, metadata: dict[str, object]
) -> None:
    with pytest.raises(ValueError, match="looks sensitive"):
        await AuditService(db_session).record(
            AuditAction.AUTH_LOGIN_FAILED, entity_type="user", metadata=metadata
        )
    await db_session.commit()

    assert await _audit_row_count(db_session) == 0


@pytest.mark.parametrize(
    "metadata",
    [
        {"target_user_id": uuid.uuid4()},
        {"amount": Decimal("1.5")},
        {"when": object()},
        {"values": (1, 2)},
        {"ratio": float("nan")},
        {"nested": {1: "non-string key"}},
    ],
)
async def test_record_rejects_metadata_that_is_not_plain_json(
    db_session: AsyncSession, metadata: dict[str, object]
) -> None:
    with pytest.raises(ValueError, match="Audit metadata"):
        await AuditService(db_session).record(
            AuditAction.MEMBER_ADDED, entity_type="workspace_member", metadata=metadata
        )
    await db_session.commit()

    assert await _audit_row_count(db_session) == 0


async def test_record_accepts_keys_that_only_resemble_ip(db_session: AsyncSession) -> None:
    metadata = {"description": "x", "membership_id": "y", "recipient_count": 2, "zip": None}

    entry = await AuditService(db_session).record(
        AuditAction.PRODUCT_CREATED, entity_type="product", metadata=metadata
    )
    await db_session.commit()
    db_session.expunge_all()

    stored = await db_session.get_one(AuditLog, entry.id)
    assert stored.action_metadata == metadata


async def test_audit_rows_survive_deleting_the_actor(db_session: AsyncSession) -> None:
    owner = await _add_user(db_session, "owner@example.com")
    actor = await _add_user(db_session, "actor@example.com")
    workspace = await _add_workspace(db_session, owner)
    entry = await AuditService(db_session).record(
        AuditAction.WORKSPACE_CREATED,
        entity_type="workspace",
        entity_id=workspace.id,
        actor_id=actor.id,
        workspace_id=workspace.id,
    )
    await db_session.commit()

    await db_session.execute(delete(User).where(User.id == actor.id))
    await db_session.commit()
    db_session.expunge_all()

    stored = await db_session.get_one(AuditLog, entry.id)
    assert stored.actor_id is None
    assert stored.workspace_id == workspace.id
    assert stored.action == "WORKSPACE_CREATED"


async def test_deleting_a_workspace_with_audit_rows_is_refused(db_session: AsyncSession) -> None:
    owner = await _add_user(db_session, "owner@example.com")
    workspace = await _add_workspace(db_session, owner)
    workspace_id = workspace.id  # the rollback below expires the ORM object
    await AuditService(db_session).record(
        AuditAction.WORKSPACE_CREATED,
        entity_type="workspace",
        entity_id=workspace_id,
        actor_id=owner.id,
        workspace_id=workspace_id,
    )
    await db_session.commit()

    with pytest.raises(IntegrityError, match="fk_audit_logs_workspace_id_workspaces"):
        await db_session.execute(delete(Workspace).where(Workspace.id == workspace_id))
    await db_session.rollback()

    assert await db_session.get(Workspace, workspace_id) is not None
    assert await _audit_row_count(db_session) == 1
