"""Background jobs (parse, validate, import) run against a BOM upload.

At most one job per upload may be active (QUEUED or RUNNING); a partial unique index enforces
it. Jobs are removed together with their upload (ON DELETE CASCADE).
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
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
from app.models.enums import JobStatus, JobType


class ImportJob(Base):
    __tablename__ = "import_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    bom_upload_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("bom_uploads.id", ondelete="CASCADE"), index=True
    )
    job_type: Mapped[JobType] = mapped_column(Enum(JobType, name="job_type"))
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="job_status"),
        server_default=text("'QUEUED'::job_status"),
    )
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    celery_task_id: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    requested_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    queued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        Index(
            "uq_import_jobs_bom_upload_id_active",
            bom_upload_id,
            unique=True,
            postgresql_where=text("status IN ('QUEUED', 'RUNNING')"),
        ),
    )
