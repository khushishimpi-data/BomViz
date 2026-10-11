"""Queries on `bom_uploads` and `import_jobs`.

BOM uploads are tenant-owned through `workspace_id`: every list or lookup filters by it, or
reaches the upload through the caller's membership of its workspace (one scoped query, never a
fetch-then-compare). Soft-deleted uploads are invisible here. Jobs have no `workspace_id` and are
only queried for uploads resolved that way.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BomUpload,
    ImportJob,
    Product,
    ProductRevision,
    UploadStatus,
    UserRole,
    WorkspaceMember,
)
from app.repositories.product_repository import LIKE_ESCAPE, escape_like


@dataclass(frozen=True, slots=True)
class BomAccess:
    """A BOM upload together with the role the current user holds in its workspace."""

    bom: BomUpload
    role: UserRole


class BomRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_access(self, bom_id: uuid.UUID, user_id: uuid.UUID) -> BomAccess | None:
        """The upload and `user_id`'s role in its workspace, in one scoped query.

        None when the upload does not exist, is soft-deleted, or the user is not a member of its
        workspace; callers cannot tell these apart.
        """
        result = await self._session.execute(
            select(BomUpload, WorkspaceMember.role)
            .join(
                WorkspaceMember,
                and_(
                    WorkspaceMember.workspace_id == BomUpload.workspace_id,
                    WorkspaceMember.user_id == user_id,
                ),
            )
            .where(BomUpload.id == bom_id, BomUpload.is_deleted.is_(False))
        )
        row = result.one_or_none()
        if row is None:
            return None
        bom, role = row
        return BomAccess(bom=bom, role=role)

    async def find_duplicate(
        self, workspace_id: uuid.UUID, checksum_sha256: str
    ) -> uuid.UUID | None:
        """Id of a non-deleted upload of the same bytes in `workspace_id`, if any."""
        result: uuid.UUID | None = await self._session.scalar(
            select(BomUpload.id).where(
                BomUpload.workspace_id == workspace_id,
                BomUpload.checksum_sha256 == checksum_sha256,
                BomUpload.is_deleted.is_(False),
            )
        )
        return result

    async def product_in_workspace(self, product_id: uuid.UUID, workspace_id: uuid.UUID) -> bool:
        found = await self._session.scalar(
            select(Product.id).where(Product.id == product_id, Product.workspace_id == workspace_id)
        )
        return found is not None

    async def revision_of_product(self, revision_id: uuid.UUID, product_id: uuid.UUID) -> bool:
        found = await self._session.scalar(
            select(ProductRevision.id).where(
                ProductRevision.id == revision_id, ProductRevision.product_id == product_id
            )
        )
        return found is not None

    async def add_upload(self, bom: BomUpload, job: ImportJob) -> None:
        """Insert `bom` and its first job inside a savepoint.

        A duplicate checksum raises `IntegrityError`; only the savepoint is rolled back, so the
        caller's transaction stays usable.
        """
        async with self._session.begin_nested():
            self._session.add(bom)
            await self._session.flush()  # the job's foreign key needs the upload row
            self._session.add(job)

    async def list_boms(
        self,
        workspace_id: uuid.UUID,
        *,
        q: str | None,
        status: UploadStatus | None,
        product_id: uuid.UUID | None,
        limit: int,
        offset: int,
    ) -> tuple[list[BomUpload], int]:
        """Uploads of `workspace_id`, newest first, and the total matching the filters.

        `q` matches a case-insensitive substring of the BOM name, original filename or revision
        label, taken literally.
        """
        conditions: list[ColumnElement[bool]] = [
            BomUpload.workspace_id == workspace_id,
            BomUpload.is_deleted.is_(False),
        ]
        if q:
            pattern = f"%{escape_like(q)}%"
            conditions.append(
                or_(
                    BomUpload.bom_name.ilike(pattern, escape=LIKE_ESCAPE),
                    BomUpload.original_filename.ilike(pattern, escape=LIKE_ESCAPE),
                    BomUpload.revision_label.ilike(pattern, escape=LIKE_ESCAPE),
                )
            )
        if status is not None:
            conditions.append(BomUpload.status == status)
        if product_id is not None:
            conditions.append(BomUpload.product_id == product_id)

        total = await self._session.scalar(
            select(func.count()).select_from(BomUpload).where(*conditions)
        )
        result = await self._session.scalars(
            select(BomUpload)
            .where(*conditions)
            .order_by(BomUpload.uploaded_at.desc(), BomUpload.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.all()), total or 0

    async def list_jobs(self, bom_id: uuid.UUID, *, limit: int) -> list[ImportJob]:
        """The most recent jobs of `bom_id`, newest first."""
        result = await self._session.scalars(
            select(ImportJob)
            .where(ImportJob.bom_upload_id == bom_id)
            .order_by(ImportJob.queued_at.desc(), ImportJob.created_at.desc(), ImportJob.id.desc())
            .limit(limit)
        )
        return list(result.all())

    async def latest_jobs(self, bom_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, ImportJob]:
        """The newest job of each upload in `bom_ids` (uploads without jobs are absent)."""
        if not bom_ids:
            return {}
        result = await self._session.scalars(
            select(ImportJob)
            .where(ImportJob.bom_upload_id.in_(bom_ids))
            .ext(distinct_on(ImportJob.bom_upload_id))
            .order_by(
                ImportJob.bom_upload_id,
                ImportJob.queued_at.desc(),
                ImportJob.created_at.desc(),
                ImportJob.id.desc(),
            )
        )
        return {job.bom_upload_id: job for job in result.all()}
