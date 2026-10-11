"""Workspaces and their members: creation with slug rules, membership management, audits.

Every change is audited in the same transaction. A workspace always keeps at least one ADMIN:
demoting or removing an ADMIN locks the workspace's ADMIN rows first, so two concurrent
demotions cannot both succeed.
"""

import re
import secrets
import unicodedata
import uuid
from collections.abc import Callable
from http import HTTPStatus

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.models import User, UserRole, Workspace, WorkspaceMember
from app.repositories.user_repository import UserRepository
from app.repositories.workspace_repository import (
    MemberRow,
    WorkspaceAccess,
    WorkspaceRepository,
    WorkspaceWithRole,
)
from app.services.audit_service import AuditAction, AuditService
from app.services.auth_service import normalize_email

WORKSPACE_NAME_MAX_LENGTH = 100
SLUG_MIN_LENGTH = 3
SLUG_MAX_LENGTH = 48
SLUG_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
GENERATED_SLUG_MAX_LENGTH = 40
FALLBACK_SLUG = "workspace"
SLUG_SUFFIX_ATTEMPTS = 5

_SLUG_CONSTRAINT = "uq_workspaces_slug"
_MEMBER_CONSTRAINT = "uq_workspace_members_workspace_id_user_id"
_NON_SLUG_CHARS = re.compile(r"[^a-z0-9]+")


class WorkspaceNotFoundError(AppError):
    """Nonexistent workspace, or one the caller is not a member of - indistinguishable."""

    status_code = HTTPStatus.NOT_FOUND
    code = "NOT_FOUND"

    def __init__(self) -> None:
        super().__init__("Workspace not found.")


class ForbiddenError(AppError):
    status_code = HTTPStatus.FORBIDDEN
    code = "FORBIDDEN"

    def __init__(self) -> None:
        super().__init__("You do not have permission to perform this action.")


class MemberNotFoundError(AppError):
    status_code = HTTPStatus.NOT_FOUND
    code = "NOT_FOUND"

    def __init__(self) -> None:
        super().__init__("Member not found.")


class UserNotFoundError(AppError):
    status_code = HTTPStatus.NOT_FOUND
    code = "USER_NOT_FOUND"

    def __init__(self) -> None:
        super().__init__("No account exists with that email.")


class AlreadyMemberError(AppError):
    status_code = HTTPStatus.CONFLICT
    code = "ALREADY_MEMBER"

    def __init__(self) -> None:
        super().__init__("That user is already a member of this workspace.")


class SlugTakenError(AppError):
    status_code = HTTPStatus.CONFLICT
    code = "SLUG_TAKEN"

    def __init__(self, message: str = "That slug is already in use.") -> None:
        super().__init__(message)


class LastAdminError(AppError):
    status_code = HTTPStatus.CONFLICT
    code = "LAST_ADMIN"

    def __init__(self) -> None:
        super().__init__("A workspace must keep at least one admin.")


def slugify(name: str) -> str:
    """ASCII, lowercase, hyphen-separated, at most 40 characters; "workspace" if nothing is left."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    slug = _NON_SLUG_CHARS.sub("-", ascii_name.lower()).strip("-")
    slug = slug[:GENERATED_SLUG_MAX_LENGTH].rstrip("-")
    return slug or FALLBACK_SLUG


def random_slug_suffix() -> str:
    """Four random lowercase hex characters."""
    return secrets.token_hex(2)


def _is_violation(exc: IntegrityError, constraint: str) -> bool:
    return constraint in str(exc.orig)


class WorkspaceService:
    def __init__(
        self, session: AsyncSession, *, slug_suffix: Callable[[], str] = random_slug_suffix
    ) -> None:
        self._workspaces = WorkspaceRepository(session)
        self._users = UserRepository(session)
        self._audit = AuditService(session)
        self._slug_suffix = slug_suffix

    async def create(self, creator: User, name: str, slug: str | None) -> WorkspaceWithRole:
        """Create a workspace owned by `creator`, who becomes its ADMIN member.

        A supplied `slug` that is taken -> `SlugTakenError`. Without one, the slug is derived
        from `name` and, on collision (or when shorter than 3 characters), suffixed with
        "-" + random hex up to five times.
        """
        if slug is not None:
            workspace = await self._insert_workspace(name, slug, creator.id)
            if workspace is None:
                raise SlugTakenError()
        else:
            base = slugify(name)
            # A base shorter than the minimum slug length is never used bare: it always gets a
            # suffix, so derived slugs obey the same 3-48 rule as supplied ones.
            workspace = None
            if len(base) >= SLUG_MIN_LENGTH:
                workspace = await self._insert_workspace(name, base, creator.id)
            for _ in range(SLUG_SUFFIX_ATTEMPTS):
                if workspace is not None:
                    break
                candidate = f"{base}-{self._slug_suffix()}"
                workspace = await self._insert_workspace(name, candidate, creator.id)
            if workspace is None:
                raise SlugTakenError("Could not generate a unique slug; please choose one.")

        membership = WorkspaceMember(
            workspace_id=workspace.id, user_id=creator.id, role=UserRole.ADMIN
        )
        await self._workspaces.add_member(membership)
        await self._audit.record(
            AuditAction.WORKSPACE_CREATED,
            entity_type="workspace",
            entity_id=workspace.id,
            actor_id=creator.id,
            workspace_id=workspace.id,
            metadata={"slug": workspace.slug},
        )
        return WorkspaceWithRole(workspace=workspace, role=membership.role)

    async def _insert_workspace(
        self, name: str, slug: str, owner_id: uuid.UUID
    ) -> Workspace | None:
        """Insert in a savepoint; None if the slug is taken (the transaction stays usable)."""
        try:
            return await self._workspaces.add_workspace(
                Workspace(name=name, slug=slug, owner_id=owner_id)
            )
        except IntegrityError as exc:
            if _is_violation(exc, _SLUG_CONSTRAINT):
                return None
            raise

    async def list_for_user(
        self, user: User, *, limit: int, offset: int
    ) -> tuple[list[WorkspaceWithRole], int]:
        return await self._workspaces.list_for_user(user.id, limit=limit, offset=offset)

    async def list_members(
        self, access: WorkspaceAccess, *, limit: int, offset: int
    ) -> tuple[list[MemberRow], int]:
        return await self._workspaces.list_members(access.workspace.id, limit=limit, offset=offset)

    async def add_member(
        self, access: WorkspaceAccess, actor: User, email: str, role: UserRole
    ) -> MemberRow:
        """Add an existing, active user by email. Unknown or inactive -> `UserNotFoundError`."""
        workspace_id = access.workspace.id
        user = await self._users.get_by_email(normalize_email(email))
        if user is None or not user.is_active:
            raise UserNotFoundError()
        if await self._workspaces.find_member_by_user(workspace_id, user.id) is not None:
            raise AlreadyMemberError()
        membership = WorkspaceMember(workspace_id=workspace_id, user_id=user.id, role=role)
        try:
            await self._workspaces.add_member(membership)
        except IntegrityError as exc:
            # A concurrent request added the same user first.
            if _is_violation(exc, _MEMBER_CONSTRAINT):
                raise AlreadyMemberError() from exc
            raise
        await self._audit.record(
            AuditAction.MEMBER_ADDED,
            entity_type="workspace_member",
            entity_id=membership.id,
            actor_id=actor.id,
            workspace_id=workspace_id,
            metadata={"target_user_id": str(user.id), "role": role.value},
        )
        return MemberRow(membership=membership, user=user)

    async def change_role(
        self, access: WorkspaceAccess, actor: User, member_id: uuid.UUID, role: UserRole
    ) -> MemberRow:
        """Change a member's role; the same role is a no-op (nothing audited)."""
        workspace_id = access.workspace.id
        row = await self._get_member_guarding_last_admin(
            workspace_id, member_id, losing_admin=role != UserRole.ADMIN
        )
        previous = row.membership.role
        if previous == role:
            return row
        row.membership.role = role
        await self._workspaces.flush()
        await self._audit.record(
            AuditAction.MEMBER_ROLE_CHANGED,
            entity_type="workspace_member",
            entity_id=row.membership.id,
            actor_id=actor.id,
            workspace_id=workspace_id,
            metadata={
                "target_user_id": str(row.membership.user_id),
                "from": previous.value,
                "to": role.value,
            },
        )
        return row

    async def remove_member(
        self, access: WorkspaceAccess, actor: User, member_id: uuid.UUID
    ) -> None:
        """Delete only the membership row (members may remove themselves) and audit it."""
        workspace_id = access.workspace.id
        row = await self._get_member_guarding_last_admin(workspace_id, member_id, losing_admin=True)
        membership = row.membership
        await self._audit.record(
            AuditAction.MEMBER_REMOVED,
            entity_type="workspace_member",
            entity_id=membership.id,
            actor_id=actor.id,
            workspace_id=workspace_id,
            metadata={
                "target_user_id": str(membership.user_id),
                "previous_role": membership.role.value,
            },
        )
        await self._workspaces.delete_member(membership)

    async def _get_member_guarding_last_admin(
        self, workspace_id: uuid.UUID, member_id: uuid.UUID, *, losing_admin: bool
    ) -> MemberRow:
        """Load the member; if it is an ADMIN about to lose that role, enforce `LAST_ADMIN`.

        The ADMIN rows are locked before counting, and stay locked until the transaction ends,
        so a concurrent demotion waits and then sees this one's outcome.
        """
        row = await self._workspaces.get_member(workspace_id, member_id)
        if row is None:
            raise MemberNotFoundError()
        if not losing_admin or row.membership.role != UserRole.ADMIN:
            return row

        admins = await self._workspaces.lock_admins(workspace_id)
        if any(admin.id == row.membership.id for admin in admins):
            if len(admins) <= 1:
                raise LastAdminError()
            return row
        # Demoted or removed by another request while we waited for the lock.
        refreshed = await self._workspaces.get_member(workspace_id, member_id, refresh=True)
        if refreshed is None:
            raise MemberNotFoundError()
        return refreshed
