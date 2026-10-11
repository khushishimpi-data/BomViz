"""Queries on `workspaces` and `workspace_members`.

Every query on `workspace_members` filters by `workspace_id` (the tenant key), and a member id is
only ever resolved together with the workspace it must belong to, so an id from another
workspace simply finds nothing.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, UserRole, Workspace, WorkspaceMember


@dataclass(frozen=True, slots=True)
class WorkspaceAccess:
    """A workspace together with the current user's membership in it."""

    workspace: Workspace
    membership: WorkspaceMember


@dataclass(frozen=True, slots=True)
class WorkspaceWithRole:
    """A workspace and the role a given user holds in it."""

    workspace: Workspace
    role: UserRole


@dataclass(frozen=True, slots=True)
class MemberRow:
    """A membership joined with the user's public profile."""

    membership: WorkspaceMember
    user: User

    @property
    def id(self) -> uuid.UUID:
        return self.membership.id

    @property
    def user_id(self) -> uuid.UUID:
        return self.membership.user_id

    @property
    def email(self) -> str:
        return self.user.email

    @property
    def full_name(self) -> str:
        return self.user.full_name

    @property
    def role(self) -> UserRole:
        return self.membership.role

    @property
    def created_at(self) -> datetime:
        return self.membership.created_at


class WorkspaceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_access(
        self, workspace_id: uuid.UUID, user_id: uuid.UUID
    ) -> WorkspaceAccess | None:
        """The workspace and `user_id`'s membership, in one scoped query.

        Returns None both when the workspace does not exist and when the user is not a member,
        so callers cannot tell the two apart.
        """
        result = await self._session.execute(
            select(Workspace, WorkspaceMember)
            .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
            .where(Workspace.id == workspace_id, WorkspaceMember.user_id == user_id)
        )
        row = result.one_or_none()
        if row is None:
            return None
        workspace, membership = row
        return WorkspaceAccess(workspace=workspace, membership=membership)

    async def add_workspace(self, workspace: Workspace) -> Workspace:
        """Insert `workspace` inside a savepoint.

        A slug collision raises `IntegrityError` and rolls back only the savepoint (expunging
        `workspace`), so the caller's transaction stays usable for another attempt.
        """
        async with self._session.begin_nested():
            self._session.add(workspace)
        return workspace

    async def add_member(self, member: WorkspaceMember) -> WorkspaceMember:
        """Insert `member` inside a savepoint; a duplicate raises `IntegrityError`."""
        async with self._session.begin_nested():
            self._session.add(member)
        return member

    async def list_for_user(
        self, user_id: uuid.UUID, *, limit: int, offset: int
    ) -> tuple[list[WorkspaceWithRole], int]:
        """The workspaces `user_id` belongs to, ordered by name, and their total count."""
        total = await self._session.scalar(
            select(func.count())
            .select_from(WorkspaceMember)
            .where(WorkspaceMember.user_id == user_id)
        )
        result = await self._session.execute(
            select(Workspace, WorkspaceMember.role)
            .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
            .where(WorkspaceMember.user_id == user_id)
            .order_by(Workspace.name, Workspace.id)
            .limit(limit)
            .offset(offset)
        )
        items = [WorkspaceWithRole(workspace=ws, role=role) for ws, role in result.all()]
        return items, total or 0

    async def list_members(
        self, workspace_id: uuid.UUID, *, limit: int, offset: int
    ) -> tuple[list[MemberRow], int]:
        """Members of `workspace_id` ordered by full name then email, and their total count."""
        total = await self._session.scalar(
            select(func.count())
            .select_from(WorkspaceMember)
            .where(WorkspaceMember.workspace_id == workspace_id)
        )
        result = await self._session.execute(
            select(WorkspaceMember, User)
            .join(User, User.id == WorkspaceMember.user_id)
            .where(WorkspaceMember.workspace_id == workspace_id)
            .order_by(User.full_name, User.email, WorkspaceMember.id)
            .limit(limit)
            .offset(offset)
        )
        items = [MemberRow(membership=member, user=user) for member, user in result.all()]
        return items, total or 0

    async def get_member(
        self, workspace_id: uuid.UUID, member_id: uuid.UUID, *, refresh: bool = False
    ) -> MemberRow | None:
        """Membership `member_id` of `workspace_id` with its user, or None.

        `refresh=True` overwrites any copy already in the session with the current row.
        """
        statement = (
            select(WorkspaceMember, User)
            .join(User, User.id == WorkspaceMember.user_id)
            .where(WorkspaceMember.id == member_id, WorkspaceMember.workspace_id == workspace_id)
        )
        if refresh:
            statement = statement.execution_options(populate_existing=True)
        row = (await self._session.execute(statement)).one_or_none()
        if row is None:
            return None
        member, user = row
        return MemberRow(membership=member, user=user)

    async def find_member_by_user(
        self, workspace_id: uuid.UUID, user_id: uuid.UUID
    ) -> WorkspaceMember | None:
        result = await self._session.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.user_id == user_id
            )
        )
        return result.scalar_one_or_none()

    async def lock_admins(self, workspace_id: uuid.UUID) -> list[WorkspaceMember]:
        """Lock and return the workspace's ADMIN memberships (`SELECT ... FOR UPDATE`).

        Rows are locked in id order so concurrent callers queue instead of deadlocking. Under
        READ COMMITTED a caller that waited re-checks each row after the lock holder commits, so
        it sees admins demoted or removed in the meantime. Loaded copies are refreshed.
        """
        result = await self._session.execute(
            select(WorkspaceMember)
            .where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.role == UserRole.ADMIN,
            )
            .order_by(WorkspaceMember.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return list(result.scalars().all())

    async def delete_member(self, member: WorkspaceMember) -> None:
        """Delete only the membership row; the user and their audit history are untouched."""
        await self._session.delete(member)
        await self._session.flush()

    async def flush(self) -> None:
        await self._session.flush()
