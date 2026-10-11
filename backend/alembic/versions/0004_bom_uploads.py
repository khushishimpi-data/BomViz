"""BOM uploads and background jobs.

- enum types file_format, upload_status (full lifecycle, Phase 2 uses the first four values),
  job_type, job_status
- bom_uploads: workspace_id ON DELETE RESTRICT, CHECK size_bytes > 0, UNIQUE
  original_file_key, partial UNIQUE (workspace_id, checksum_sha256) WHERE NOT is_deleted (A6)
- import_jobs: bom_upload_id ON DELETE CASCADE, CHECK attempts >= 0, partial UNIQUE
  (bom_upload_id) WHERE status IN ('QUEUED', 'RUNNING') (one active job per BOM)

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

file_format = postgresql.ENUM("CSV", "XLSX", "XLS", name="file_format", create_type=False)
upload_status = postgresql.ENUM(
    "UPLOADED",
    "PARSING",
    "PARSED",
    "PARSE_FAILED",
    "MAPPED",
    "VALIDATING",
    "VALIDATED",
    "VALIDATION_FAILED",
    "IMPORTING",
    "IMPORTED",
    "IMPORT_FAILED",
    name="upload_status",
    create_type=False,
)
job_type = postgresql.ENUM("PARSE", "VALIDATE", "IMPORT", name="job_type", create_type=False)
job_status = postgresql.ENUM(
    "QUEUED", "RUNNING", "SUCCEEDED", "FAILED", name="job_status", create_type=False
)


def _timestamps() -> list[sa.Column[object]]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
    ]


def upgrade() -> None:
    bind = op.get_bind()
    file_format.create(bind)
    upload_status.create(bind)
    job_type.create(bind)
    job_status.create(bind)

    op.create_table(
        "bom_uploads",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=True),
        sa.Column("product_revision_id", sa.Uuid(), nullable=True),
        sa.Column("bom_name", sa.Text(), nullable=False),
        sa.Column("revision_label", sa.Text(), nullable=True),
        sa.Column("original_filename", sa.Text(), nullable=False),
        sa.Column("file_format", file_format, nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("checksum_sha256", sa.CHAR(64), nullable=False),
        sa.Column("original_file_key", sa.Text(), nullable=False),
        sa.Column(
            "status",
            upload_status,
            server_default=sa.text("'UPLOADED'::upload_status"),
            nullable=False,
        ),
        sa.Column("parse_options", postgresql.JSONB(), nullable=True),
        sa.Column("detected_columns", postgresql.JSONB(), nullable=True),
        sa.Column("parse_result", postgresql.JSONB(), nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column("column_count", sa.Integer(), nullable=True),
        sa.Column("uploaded_by_id", sa.Uuid(), nullable=True),
        sa.Column(
            "uploaded_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("is_deleted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bom_uploads")),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_bom_uploads_workspace_id_workspaces"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_bom_uploads_product_id_products"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["product_revision_id"],
            ["product_revisions.id"],
            name=op.f("fk_bom_uploads_product_revision_id_product_revisions"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by_id"],
            ["users.id"],
            name=op.f("fk_bom_uploads_uploaded_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("original_file_key", name=op.f("uq_bom_uploads_original_file_key")),
        sa.CheckConstraint("size_bytes > 0", name=op.f("ck_bom_uploads_size_bytes_positive")),
    )
    op.create_index(op.f("ix_bom_uploads_workspace_id"), "bom_uploads", ["workspace_id"])
    op.create_index(op.f("ix_bom_uploads_product_id"), "bom_uploads", ["product_id"])
    op.create_index(op.f("ix_bom_uploads_status"), "bom_uploads", ["status"])
    op.create_index(
        "ix_bom_uploads_workspace_id_uploaded_at",
        "bom_uploads",
        ["workspace_id", sa.text("uploaded_at DESC")],
    )
    op.create_index(
        "uq_bom_uploads_workspace_id_checksum_sha256",
        "bom_uploads",
        ["workspace_id", "checksum_sha256"],
        unique=True,
        postgresql_where=sa.text("NOT is_deleted"),
    )

    op.create_table(
        "import_jobs",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("bom_upload_id", sa.Uuid(), nullable=False),
        sa.Column("job_type", job_type, nullable=False),
        sa.Column(
            "status",
            job_status,
            server_default=sa.text("'QUEUED'::job_status"),
            nullable=False,
        ),
        sa.Column(
            "parameters",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("celery_task_id", sa.Text(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("requested_by_id", sa.Uuid(), nullable=True),
        sa.Column(
            "queued_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_import_jobs")),
        sa.ForeignKeyConstraint(
            ["bom_upload_id"],
            ["bom_uploads.id"],
            name=op.f("fk_import_jobs_bom_upload_id_bom_uploads"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_id"],
            ["users.id"],
            name=op.f("fk_import_jobs_requested_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_import_jobs_attempts_non_negative")),
    )
    op.create_index(op.f("ix_import_jobs_bom_upload_id"), "import_jobs", ["bom_upload_id"])
    op.create_index(
        "uq_import_jobs_bom_upload_id_active",
        "import_jobs",
        ["bom_upload_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('QUEUED', 'RUNNING')"),
    )


def downgrade() -> None:
    op.drop_index("uq_import_jobs_bom_upload_id_active", table_name="import_jobs")
    op.drop_index(op.f("ix_import_jobs_bom_upload_id"), table_name="import_jobs")
    op.drop_table("import_jobs")
    op.drop_index("uq_bom_uploads_workspace_id_checksum_sha256", table_name="bom_uploads")
    op.drop_index("ix_bom_uploads_workspace_id_uploaded_at", table_name="bom_uploads")
    op.drop_index(op.f("ix_bom_uploads_status"), table_name="bom_uploads")
    op.drop_index(op.f("ix_bom_uploads_product_id"), table_name="bom_uploads")
    op.drop_index(op.f("ix_bom_uploads_workspace_id"), table_name="bom_uploads")
    op.drop_table("bom_uploads")

    bind = op.get_bind()
    job_status.drop(bind)
    job_type.drop(bind)
    upload_status.drop(bind)
    file_format.drop(bind)
