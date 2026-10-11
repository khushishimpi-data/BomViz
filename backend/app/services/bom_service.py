"""BOM uploads: intake, immutable storage, persistence, parse enqueueing; list, detail, download.

The workspace (upload, list) or the upload (detail, file) has already been resolved through the
caller's membership by the route dependency, so nothing here trusts a client-supplied id; product
and revision ids are checked against that workspace.

Upload transaction handling (commit-before-enqueue): `upload` commits inside the service, after
the object is stored and the rows plus the `BOM_UPLOADED` audit are written, because the parse
task must only be enqueued once its job row is visible to the worker. The job's Celery task id is
generated here and committed with the job; an enqueue failure is recorded in a second transaction
(see `enqueue_parse_job`). The request-scoped session's final commit is then a no-op.

No transaction is held open while the file is copied to a temporary file or stored: the
read-only checks (the route's workspace access, product scope, the duplicate lookup) are ended
with a commit before each of those slow steps, so a slow upload never keeps a pooled connection
idle in a transaction. A concurrent duplicate that slips in meanwhile is caught by the partial
unique index at insert time.

If the insert or its commit definitely fails, the stored object is deleted again (best effort)
so no orphan is left behind; so is it when the request is cancelled (client disconnect) after
the object was stored and before the commit started. When the outcome of the commit itself is
unknown (the connection failed during COMMIT, or the request was cancelled while committing),
the row may exist, so the object is kept and `bom_upload_commit_outcome_unknown` is logged at
ERROR; the error then surfaces as the generic 500 `INTERNAL_ERROR`.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from http import HTTPStatus

from fastapi import UploadFile
from sqlalchemy.exc import DBAPIError, IntegrityError, InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import AppError
from app.models import BomUpload, FileFormat, ImportJob, JobStatus, JobType, UploadStatus, User
from app.repositories.bom_repository import BomAccess, BomRepository
from app.repositories.workspace_repository import WorkspaceAccess
from app.services.audit_service import AuditAction, AuditService
from app.services.job_enqueue import enqueue_parse_job, new_task_id
from app.services.job_queue import JobQueue
from app.services.product_service import ProductNotFoundError
from app.services.storage_service import Storage
from app.services.upload_intake import (
    CONTENT_TYPES,
    FILE_EXTENSIONS,
    detect_file_format,
    receive_upload,
    sanitize_filename,
)

logger = logging.getLogger(__name__)

BOM_NAME_MAX_LENGTH = 200
REVISION_LABEL_MAX_LENGTH = 64
SEARCH_MAX_LENGTH = 200
DETAIL_JOBS_LIMIT = 10

_CHECKSUM_CONSTRAINT = "uq_bom_uploads_workspace_id_checksum_sha256"


class BomNotFoundError(AppError):
    """Nonexistent or deleted BOM, or one in a workspace the caller is not a member of."""

    status_code = HTTPStatus.NOT_FOUND
    code = "NOT_FOUND"

    def __init__(self) -> None:
        super().__init__("BOM not found.")


class ProductRevisionNotFoundError(AppError):
    status_code = HTTPStatus.NOT_FOUND
    code = "NOT_FOUND"

    def __init__(self) -> None:
        super().__init__("Product revision not found.")


class RevisionWithoutProductError(AppError):
    status_code = HTTPStatus.UNPROCESSABLE_ENTITY
    code = "VALIDATION_ERROR"

    def __init__(self) -> None:
        super().__init__(
            "Request validation failed.",
            details=[
                {
                    "field": "body.product_revision_id",
                    "message": "A product revision requires its product_id.",
                    "type": "value_error",
                }
            ],
        )


class DuplicateUploadError(AppError):
    status_code = HTTPStatus.CONFLICT
    code = "DUPLICATE_UPLOAD"

    def __init__(self, existing_bom_id: uuid.UUID | None) -> None:
        details = [{"existing_bom_id": str(existing_bom_id)}] if existing_bom_id else []
        super().__init__("This file has already been uploaded to this workspace.", details=details)


@dataclass(frozen=True, slots=True)
class UploadMetadata:
    """Validated form fields that accompany the uploaded file."""

    bom_name: str
    revision_label: str | None = None
    product_id: uuid.UUID | None = None
    product_revision_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class BomSummary:
    bom: BomUpload
    latest_job: ImportJob | None


@dataclass(frozen=True, slots=True)
class BomDetail:
    bom: BomUpload
    jobs: list[ImportJob]  # Newest first, at most `DETAIL_JOBS_LIMIT`.

    @property
    def latest_job(self) -> ImportJob | None:
        return self.jobs[0] if self.jobs else None


def object_key(workspace_id: uuid.UUID, bom_id: uuid.UUID, file_format: FileFormat) -> str:
    """Storage key of an original file. Built from ids only, never from the client filename."""
    return f"workspaces/{workspace_id}/boms/{bom_id}/original.{FILE_EXTENSIONS[file_format]}"


def _is_violation(exc: IntegrityError, constraint: str) -> bool:
    return constraint in str(exc.orig)


def _commit_outcome_unknown(exc: Exception) -> bool:
    """True for connection-level failures, after which a COMMIT may or may not have applied."""
    if isinstance(exc, OperationalError | InterfaceError):
        return True
    return isinstance(exc, DBAPIError) and exc.connection_invalidated


def _log_commit_outcome_unknown(bom_id: uuid.UUID) -> None:
    """The stored object is kept: the upload row may have been committed."""
    logger.error(
        "bom_upload_commit_outcome_unknown",
        extra={"bom_upload_id": str(bom_id)},
        exc_info=True,
    )


class BomService:
    def __init__(self, session: AsyncSession, storage: Storage, settings: Settings) -> None:
        self._session = session
        self._boms = BomRepository(session)
        self._audit = AuditService(session)
        self._storage = storage
        self._settings = settings

    async def upload(
        self,
        access: WorkspaceAccess,
        actor: User,
        file: UploadFile,
        metadata: UploadMetadata,
        queue: JobQueue,
    ) -> BomDetail:
        """Store the file, record the upload and its parse job, then enqueue the job.

        Raises `FileTooLargeError`, `EmptyFileError`, `UnsupportedFileTypeError`,
        `EncryptedWorkbookError`, `DuplicateUploadError`, product/revision 404s and
        `StorageUnavailableError`. A failed enqueue does not fail the upload: the job is marked
        `FAILED` `QUEUE_UNAVAILABLE` and the upload `PARSE_FAILED` (the user can re-parse).
        """
        workspace_id = access.workspace.id
        await self._check_product_scope(workspace_id, metadata)
        # End the read-only transaction before the (slow) copy to a temporary file.
        await self._session.commit()

        async with receive_upload(file, self._settings.max_upload_bytes) as received:
            file_format = detect_file_format(file.filename, received.head)
            existing_id = await self._boms.find_duplicate(workspace_id, received.checksum_sha256)
            # And again before the (slow) storage upload; the unique index re-checks at insert.
            await self._session.commit()
            if existing_id is not None:
                raise DuplicateUploadError(existing_id)
            # The id is chosen here because the object key needs it before the row exists.
            bom_id = uuid.uuid4()
            key = object_key(workspace_id, bom_id, file_format)
            content_type = CONTENT_TYPES[file_format]
            await self._storage.put_file(key, received.path, content_type)

        bom = BomUpload(
            id=bom_id,
            workspace_id=workspace_id,
            product_id=metadata.product_id,
            product_revision_id=metadata.product_revision_id,
            bom_name=metadata.bom_name,
            revision_label=metadata.revision_label,
            original_filename=sanitize_filename(file.filename, file_format),
            file_format=file_format,
            content_type=content_type,
            size_bytes=received.size_bytes,
            checksum_sha256=received.checksum_sha256,
            original_file_key=key,
            status=UploadStatus.UPLOADED,
            uploaded_by_id=actor.id,
        )
        job = ImportJob(
            bom_upload_id=bom_id,
            job_type=JobType.PARSE,
            status=JobStatus.QUEUED,
            parameters={},
            requested_by_id=actor.id,
            celery_task_id=new_task_id(),
        )
        committing = False
        try:
            await self._boms.add_upload(bom, job)
            await self._audit.record(
                AuditAction.BOM_UPLOADED,
                entity_type="bom_upload",
                entity_id=bom.id,
                actor_id=actor.id,
                workspace_id=workspace_id,
                metadata={"file_format": file_format.value, "size_bytes": bom.size_bytes},
            )
            committing = True
            await self._session.commit()
        except IntegrityError as exc:
            await self._discard_object(key, bom_id)
            if not _is_violation(exc, _CHECKSUM_CONSTRAINT):
                raise
            # A concurrent upload of the same bytes won the race; point at it.
            await self._session.rollback()
            raise DuplicateUploadError(
                await self._boms.find_duplicate(workspace_id, received.checksum_sha256)
            ) from exc
        except Exception as exc:
            if committing and _commit_outcome_unknown(exc):
                _log_commit_outcome_unknown(bom_id)
            else:
                await self._discard_object(key, bom_id)
            raise
        except asyncio.CancelledError:
            if committing:
                _log_commit_outcome_unknown(bom_id)
            else:
                # Shielded so that the cleanup itself is not cancelled along with the request.
                await asyncio.shield(self._discard_object(key, bom_id))
            raise

        await enqueue_parse_job(self._session, queue, job)
        return BomDetail(bom=bom, jobs=await self._boms.list_jobs(bom.id, limit=DETAIL_JOBS_LIMIT))

    async def list_boms(
        self,
        access: WorkspaceAccess,
        *,
        q: str | None,
        status: UploadStatus | None,
        product_id: uuid.UUID | None,
        limit: int,
        offset: int,
    ) -> tuple[list[BomSummary], int]:
        """Uploads of the workspace, newest first, each with its latest job."""
        boms, total = await self._boms.list_boms(
            access.workspace.id,
            q=q,
            status=status,
            product_id=product_id,
            limit=limit,
            offset=offset,
        )
        latest = await self._boms.latest_jobs([bom.id for bom in boms])
        return [BomSummary(bom=bom, latest_job=latest.get(bom.id)) for bom in boms], total

    async def get_detail(self, access: BomAccess) -> BomDetail:
        """The resolved upload with its last jobs, newest first."""
        jobs = await self._boms.list_jobs(access.bom.id, limit=DETAIL_JOBS_LIMIT)
        return BomDetail(bom=access.bom, jobs=jobs)

    async def file_url(self, access: BomAccess, actor: User) -> tuple[str, datetime]:
        """A presigned download URL for the original file; every issued URL is audited."""
        bom = access.bom
        url, expires_at = await self._storage.presigned_download_url(
            bom.original_file_key,
            bom.original_filename,
            bom.content_type,
            self._settings.signed_url_ttl_seconds,
        )
        await self._audit.record(
            AuditAction.BOM_FILE_DOWNLOADED,
            entity_type="bom_upload",
            entity_id=bom.id,
            actor_id=actor.id,
            workspace_id=bom.workspace_id,
        )
        return url, expires_at

    async def _check_product_scope(self, workspace_id: uuid.UUID, metadata: UploadMetadata) -> None:
        """The product must be in the workspace, and the revision must belong to the product."""
        if metadata.product_id is None:
            if metadata.product_revision_id is not None:
                raise RevisionWithoutProductError()
            return
        if not await self._boms.product_in_workspace(metadata.product_id, workspace_id):
            raise ProductNotFoundError()
        if metadata.product_revision_id is not None and not await self._boms.revision_of_product(
            metadata.product_revision_id, metadata.product_id
        ):
            raise ProductRevisionNotFoundError()

    async def _discard_object(self, key: str, bom_id: uuid.UUID) -> None:
        """Best-effort removal of an object whose database row was never committed."""
        try:
            await self._storage.delete(key)
        except Exception:
            logger.warning(
                "orphaned_upload_object", extra={"bom_upload_id": str(bom_id)}, exc_info=True
            )
