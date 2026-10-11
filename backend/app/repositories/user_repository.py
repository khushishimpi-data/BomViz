"""Queries on `users`, plus the current user's workspace memberships."""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, UserRole, Workspace, WorkspaceMember


@dataclass(frozen=True, slots=True)
class MembershipSummary:
    """One workspace the user belongs to, with their role in it."""

    workspace_id: uuid.UUID
    workspace_name: str
    workspace_slug: str
    role: UserRole


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        return await self._session.get(User, user_id)

    async def get_by_email(self, email: str) -> User | None:
        """Case-insensitive lookup (`users.email` is citext)."""
        result = await self._session.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    async def add(self, user: User) -> User:
        """Insert `user` inside a savepoint.

        A unique-email violation raises `IntegrityError` and rolls back only the savepoint, so
        the caller's transaction stays usable.
        """
        async with self._session.begin_nested():
            self._session.add(user)
        return user

    async def list_memberships(self, user_id: uuid.UUID) -> list[MembershipSummary]:
        """Every workspace `user_id` is a member of, ordered by workspace name."""
        result = await self._session.execute(
            select(Workspace.id, Workspace.name, Workspace.slug, WorkspaceMember.role)
            .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
            .where(WorkspaceMember.user_id == user_id)
            .order_by(Workspace.name, Workspace.id)
        )
        return [
            MembershipSummary(
                workspace_id=workspace_id, workspace_name=name, workspace_slug=slug, role=role
            )
            for workspace_id, name, slug, role in result.all()
        ]
