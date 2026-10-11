"""The single authorization policy: which workspace role grants which permission.

Deny by default: a role without an entry, or a permission not listed for a role, is refused.
"""

from enum import StrEnum

from app.models.enums import UserRole


class Permission(StrEnum):
    """Capabilities checked inside a workspace."""

    WORKSPACE_READ = "workspace:read"
    WORKSPACE_MANAGE_MEMBERS = "workspace:manage_members"
    PRODUCT_READ = "product:read"
    PRODUCT_WRITE = "product:write"


_READ_ONLY = frozenset({Permission.WORKSPACE_READ, Permission.PRODUCT_READ})

ROLE_PERMISSIONS: dict[UserRole, frozenset[Permission]] = {
    UserRole.ADMIN: frozenset(Permission),
    UserRole.ENGINEER: _READ_ONLY | {Permission.PRODUCT_WRITE},
    UserRole.PROCUREMENT: _READ_ONLY,
    UserRole.PRODUCTION: _READ_ONLY,
    UserRole.VIEWER: _READ_ONLY,
}


def has_permission(role: UserRole | None, permission: Permission) -> bool:
    """Return True only if `role` explicitly grants `permission`."""
    if role is None:
        return False
    return permission in ROLE_PERMISSIONS.get(role, frozenset())
