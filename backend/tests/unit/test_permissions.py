from typing import Any, cast

import pytest

from app.core.permissions import ROLE_PERMISSIONS, Permission, has_permission
from app.models.enums import UserRole

R = Permission.WORKSPACE_READ
M = Permission.WORKSPACE_MANAGE_MEMBERS
PR = Permission.PRODUCT_READ
PW = Permission.PRODUCT_WRITE

EXPECTED_GRANTS: dict[UserRole, set[Permission]] = {
    UserRole.ADMIN: {R, M, PR, PW},
    UserRole.ENGINEER: {R, PR, PW},
    UserRole.PROCUREMENT: {R, PR},
    UserRole.PRODUCTION: {R, PR},
    UserRole.VIEWER: {R, PR},
}

MATRIX = [
    pytest.param(role, permission, permission in EXPECTED_GRANTS[role], id=f"{role}-{permission}")
    for role in UserRole
    for permission in Permission
]


@pytest.mark.parametrize(("role", "permission", "allowed"), MATRIX)
def test_role_permission_matrix(role: UserRole, permission: Permission, allowed: bool) -> None:
    assert has_permission(role, permission) is allowed


def test_every_role_has_a_policy_entry() -> None:
    assert set(ROLE_PERMISSIONS) == set(UserRole)


def test_expected_matrix_covers_every_role() -> None:
    assert set(EXPECTED_GRANTS) == set(UserRole)


def test_permission_values_are_stable() -> None:
    assert {p.value for p in Permission} == {
        "workspace:read",
        "workspace:manage_members",
        "product:read",
        "product:write",
    }


@pytest.mark.parametrize("permission", list(Permission))
def test_missing_role_is_denied(permission: Permission) -> None:
    assert has_permission(None, permission) is False


@pytest.mark.parametrize("permission", list(Permission))
def test_unknown_role_is_denied(permission: Permission) -> None:
    unknown_role = cast(Any, "SUPERUSER")

    assert has_permission(unknown_role, permission) is False
