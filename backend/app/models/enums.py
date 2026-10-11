"""Enumerations persisted as PostgreSQL ENUM types.

Member names equal their values, so the database stores exactly the strings below.
"""

from enum import StrEnum


class UserRole(StrEnum):
    """Role of a user inside one workspace (`user_role`)."""

    ADMIN = "ADMIN"
    ENGINEER = "ENGINEER"
    PROCUREMENT = "PROCUREMENT"
    PRODUCTION = "PRODUCTION"
    VIEWER = "VIEWER"


class ProductStatus(StrEnum):
    """Lifecycle state of a product (`product_status`)."""

    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    OBSOLETE = "OBSOLETE"


class RevisionStatus(StrEnum):
    """Lifecycle state of a product revision (`revision_status`)."""

    DRAFT = "DRAFT"
    RELEASED = "RELEASED"
    OBSOLETE = "OBSOLETE"
