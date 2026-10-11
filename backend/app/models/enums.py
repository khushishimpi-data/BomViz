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


class FileFormat(StrEnum):
    """Format of an uploaded BOM file (`file_format`)."""

    CSV = "CSV"
    XLSX = "XLSX"
    XLS = "XLS"


class UploadStatus(StrEnum):
    """Lifecycle state of a BOM upload (`upload_status`).

    The full set is created up front; Phase 2 only uses the first four values.
    """

    UPLOADED = "UPLOADED"
    PARSING = "PARSING"
    PARSED = "PARSED"
    PARSE_FAILED = "PARSE_FAILED"
    MAPPED = "MAPPED"
    VALIDATING = "VALIDATING"
    VALIDATED = "VALIDATED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    IMPORTING = "IMPORTING"
    IMPORTED = "IMPORTED"
    IMPORT_FAILED = "IMPORT_FAILED"


class JobType(StrEnum):
    """Kind of background job run against a BOM upload (`job_type`)."""

    PARSE = "PARSE"
    VALIDATE = "VALIDATE"
    IMPORT = "IMPORT"


class JobStatus(StrEnum):
    """State of a background job (`job_status`)."""

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
