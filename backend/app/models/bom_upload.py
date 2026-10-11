"""Uploaded BOM files and their parse state.

The original file is immutable in object storage (`original_file_key`); this row tracks its
metadata, status and the bounded parse snapshot. `workspace_id` uses ON DELETE RESTRICT so a
workspace with uploads cannot be removed by accident. A checksum is unique per workspace among
non-deleted uploads only (partial unique index, A6).
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import FileFormat, UploadStatus


class BomUpload(Base):
    __tablename__ = "bom_uploads"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="RESTRICT"), index=True
    )
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="SET NULL"), index=True
    )
    product_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("product_revisions.id", ondelete="SET NULL")
    )
    bom_name: Mapped[str] = mapped_column(Text)
    revision_label: Mapped[str | None] = mapped_column(Text)
    original_filename: Mapped[str] = mapped_column(Text)
    file_format: Mapped[FileFormat] = mapped_column(Enum(FileFormat, name="file_format"))
    content_type: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    checksum_sha256: Mapped[str] = mapped_column(CHAR(64))
    original_file_key: Mapped[str] = mapped_column(Text, unique=True)
    status: Mapped[UploadStatus] = mapped_column(
        Enum(UploadStatus, name="upload_status"),
        server_default=text("'UPLOADED'::upload_status"),
        index=True,
    )
    parse_options: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    detected_columns: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    parse_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    row_count: Mapped[int | None] = mapped_column(Integer)
    column_count: Mapped[int | None] = mapped_column(Integer)
    uploaded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    is_deleted: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("size_bytes > 0", name="size_bytes_positive"),
        Index("ix_bom_uploads_workspace_id_uploaded_at", workspace_id, uploaded_at.desc()),
        Index(
            "uq_bom_uploads_workspace_id_checksum_sha256",
            workspace_id,
            checksum_sha256,
            unique=True,
            postgresql_where=text("NOT is_deleted"),
        ),
    )
