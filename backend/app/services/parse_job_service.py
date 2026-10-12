"""Parse jobs: requesting a (re-)parse from the API, and running a parse job in the worker.

Worker side (`run_parse_job`, `mark_job_failed`) runs outside any request: it is given a session
factory, the object storage and the settings, and commits every state change itself. Each
failure path commits its job/upload state before returning or raising, so a crash never leaves a
job `RUNNING` because of a failure this module anticipated.

Lock order is always the upload row first, then the job row (`run_parse_job`, `mark_job_failed`
and `ParseRequestService.request_parse` all follow it), so the API and the worker cannot
deadlock on each other.

Job `parameters` hold the options the user asked for (only the fields they provided). Stored
parameters that `ParseOptions` rejects (an unknown key or a value outside the allowlists; the
API validates them, so this only happens if a row was written some other way) fail the job with
`PARSE_INVALID_OPTIONS`, like a parse error the user can act on by re-parsing.

Ownership: the API stores the Celery task id on the job before enqueueing it (`job_enqueue`),
and the worker runs a job only as that task:

- `QUEUED` with the same task id (or none, for jobs queued before ids were stored): run.
- `RUNNING` with the same task id: a retry or redelivery of the run that started it (the worker
  died, or a database error hit the final commit). Run again while `attempts < MAX_ATTEMPTS`;
  after that the job is failed `INTERNAL_ERROR` instead of looping forever.
- anything else (another task's job, a finished job): no-op, logged.

Every run increments `attempts`. Fencing: the outcome of a run (success, failure, requeue) is
written only after re-locking the upload and the job and checking the job is still `RUNNING`
under the same task id and attempt; otherwise (it was replaced as stale, or a redelivered copy
took over) the result is dropped and logged as `parse_job_result_discarded`.

Time limits: `boms.parse` has a soft time limit. Its `SoftTimeLimitExceeded` interrupts the
parse (see `_download_and_parse`) and fails the job `PARSE_FILE_TOO_COMPLEX`, fenced like any
other failure.

Retries: only a transient storage failure (`StorageUnavailableError`) is retryable. The job is
put back to `QUEUED` with `error_code` `STORAGE_UNAVAILABLE` (the upload stays `PARSING`, so
the BOM still shows a job in progress), the state is committed, and `RetryableJobError` is
raised for the Celery task to schedule a retry (same task id). When the task runs out of
retries it calls `mark_job_failed`.
"""

import dataclasses
import enum
import logging
import tempfile
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from pathlib import Path
from typing import Any

from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.exceptions import AppError
from app.models import BomUpload, ImportJob, JobStatus, JobType, UploadStatus, User
from app.repositories.bom_repository import BomAccess
from app.services.audit_service import AuditAction, AuditService
from app.services.job_enqueue import enqueue_parse_job, new_task_id
from app.services.job_queue import JobQueue
from app.services.mapping_suggestions import count_standard_field_matches, suggest_mapping
from app.services.parsing import (
    ParseError,
    ParseErrorCode,
    ParseLimits,
    ParseOptions,
    ParseOutcome,
    parse_file,
)
from app.services.storage_service import (
    Storage,
    StorageObjectNotFoundError,
    StorageUnavailableError,
)
from app.services.upload_intake import FILE_EXTENSIONS

logger = logging.getLogger(__name__)

ACTIVE_JOB_STATUSES = (JobStatus.QUEUED, JobStatus.RUNNING)
# Runs of one job (first run plus retries/redeliveries) before a `RUNNING` job is given up.
MAX_ATTEMPTS = 3
REPARSE_ALLOWED_STATUSES = frozenset(
    {UploadStatus.UPLOADED, UploadStatus.PARSED, UploadStatus.PARSE_FAILED}
)

# Job-level error codes set by this module (parser codes come from `ParseErrorCode`).
INTERNAL_ERROR = "INTERNAL_ERROR"
INTERNAL_ERROR_MESSAGE = "The file could not be parsed."
INVALID_OPTIONS = "PARSE_INVALID_OPTIONS"
INVALID_OPTIONS_MESSAGE = "The stored parse options are invalid. Start parsing it again."
STORAGE_UNAVAILABLE = "STORAGE_UNAVAILABLE"
STORAGE_UNAVAILABLE_MESSAGE = (
    "File storage was unavailable while parsing. Start parsing it again later."
)
TIME_LIMIT_MESSAGE = "The file took too long to parse."
STALE_JOB = "STALE_JOB"
STALE_JOB_MESSAGE = "The parse job did not finish in time and was replaced by a new one."

_ACTIVE_JOB_INDEX = "uq_import_jobs_bom_upload_id_active"


def utc_now() -> datetime:
    return datetime.now(UTC)


class RetryableJobError(Exception):
    """The job hit a transient failure; its state was committed as `QUEUED` and it may be
    retried. Internal: never shown to clients."""


class JobInProgressError(AppError):
    status_code = HTTPStatus.CONFLICT
    code = "JOB_IN_PROGRESS"

    def __init__(self) -> None:
        super().__init__("A job is already running for this BOM. Wait for it to finish.")


class InvalidStateError(AppError):
    status_code = HTTPStatus.CONFLICT
    code = "INVALID_STATE"

    def __init__(self, status: UploadStatus) -> None:
        super().__init__(f"The BOM cannot be parsed while it is {status.value}.")


class NotParsedError(AppError):
    status_code = HTTPStatus.CONFLICT
    code = "NOT_PARSED"

    def __init__(self) -> None:
        super().__init__("The BOM has not been parsed successfully yet.")


class _InvalidStoredOptionsError(Exception):
    """`job.parameters` cannot be turned into `ParseOptions`."""


@dataclasses.dataclass(frozen=True, slots=True)
class _ParseSnapshot:
    """What a successful parse writes to the upload row."""

    parse_options: dict[str, Any]
    detected_columns: list[dict[str, Any]]
    row_count: int
    column_count: int
    parse_result: dict[str, Any]


def _options_from_parameters(parameters: dict[str, Any]) -> ParseOptions:
    try:
        return ParseOptions(**parameters)
    except (TypeError, ValueError) as exc:
        raise _InvalidStoredOptionsError(str(exc)) from exc


def _snapshot(outcome: ParseOutcome) -> _ParseSnapshot:
    """Plain-JSON form of a parse outcome and its suggested mapping."""
    mapping = suggest_mapping(outcome.columns)
    columns = [dataclasses.asdict(column) for column in outcome.columns]
    return _ParseSnapshot(
        parse_options=dataclasses.asdict(outcome.effective_options),
        detected_columns=columns,
        row_count=outcome.row_count,
        column_count=outcome.column_count,
        parse_result={
            "sheets": [dataclasses.asdict(sheet) for sheet in outcome.sheets],
            "columns": columns,
            "row_count": outcome.row_count,
            "column_count": outcome.column_count,
            "skipped_empty_rows": outcome.skipped_empty_rows,
            "formula_cells_without_value": outcome.formula_cells_without_value,
            "rows": [dataclasses.asdict(row) for row in outcome.sample_rows],
            "suggested_mapping": [dataclasses.asdict(column) for column in mapping.columns],
            "missing_required": list(mapping.missing_required),
        },
    )


async def _lock_upload(session: AsyncSession, bom_id: uuid.UUID) -> BomUpload | None:
    result = await session.scalars(
        select(BomUpload)
        .where(BomUpload.id == bom_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return result.one_or_none()


async def _lock_job_with_upload(
    session: AsyncSession, job_id: uuid.UUID
) -> tuple[ImportJob, BomUpload] | None:
    """The job and its upload, both locked (upload first, see the module docstring)."""
    bom_id = await session.scalar(select(ImportJob.bom_upload_id).where(ImportJob.id == job_id))
    if bom_id is None:
        return None
    bom = await _lock_upload(session, bom_id)
    job = (
        await session.scalars(
            select(ImportJob)
            .where(ImportJob.id == job_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).one_or_none()
    if bom is None or job is None:
        return None
    return job, bom


async def _fail_job(
    session: AsyncSession,
    job: ImportJob,
    bom: BomUpload,
    *,
    error_code: str,
    error_message: str,
    finished_at: datetime,
) -> None:
    """Mark the locked job `FAILED` and the upload `PARSE_FAILED`, audit it, and commit."""
    job.status = JobStatus.FAILED
    job.error_code = error_code
    job.error_message = error_message
    job.finished_at = finished_at
    bom.status = UploadStatus.PARSE_FAILED
    await AuditService(session).record(
        AuditAction.BOM_PARSE_FAILED,
        entity_type="bom_upload",
        entity_id=bom.id,
        actor_id=job.requested_by_id,
        workspace_id=bom.workspace_id,
        metadata={"error_code": error_code},
    )
    await session.commit()


class _RunDecision(enum.Enum):
    RUN = "run"
    GIVE_UP = "give_up"
    SKIP = "skip"


def _run_decision(job: ImportJob, task_id: str) -> _RunDecision:
    """Whether the task `task_id` may run `job` (see the module docstring)."""
    if job.status is JobStatus.QUEUED and job.celery_task_id in (None, task_id):
        return _RunDecision.RUN
    if job.status is JobStatus.RUNNING and job.celery_task_id == task_id:
        return _RunDecision.RUN if job.attempts < MAX_ATTEMPTS else _RunDecision.GIVE_UP
    return _RunDecision.SKIP


@dataclasses.dataclass(frozen=True, slots=True)
class _Fence:
    """The run that owns a job: only it may write the job's outcome."""

    job_id: uuid.UUID
    task_id: str
    attempt: int
    context: dict[str, str]


async def _start_job(
    session: AsyncSession, job: ImportJob, bom: BomUpload, task_id: str, started_at: datetime
) -> None:
    """Claim the job for `task_id`: `RUNNING`, one more attempt, upload `PARSING`; commit."""
    job.status = JobStatus.RUNNING
    job.celery_task_id = task_id
    job.started_at = started_at
    job.attempts += 1
    job.error_code = None
    job.error_message = None
    bom.status = UploadStatus.PARSING
    await session.commit()


async def _lock_if_owned(
    session: AsyncSession, fence: _Fence
) -> tuple[ImportJob, BomUpload] | None:
    """Re-lock the job and its upload if the run in `fence` still owns the job.

    It does while the job is `RUNNING` under the same task id and attempt. Otherwise (the job
    was replaced as stale, or a later attempt took over) the transaction is rolled back, the
    dropped result is logged, and None is returned.
    """
    locked = await _lock_job_with_upload(session, fence.job_id)
    job_status = None
    if locked is not None:
        job, _ = locked
        if (
            job.status is JobStatus.RUNNING
            and job.attempts == fence.attempt
            and job.celery_task_id == fence.task_id
        ):
            return locked
        job_status = job.status.value  # Read before the rollback expires the job.
    await session.rollback()
    logger.warning(
        "parse_job_result_discarded",
        extra={**fence.context, "attempt": fence.attempt, "job_status": job_status},
    )
    return None


async def _fail_owned_job(
    session: AsyncSession,
    fence: _Fence,
    *,
    error_code: str,
    error_message: str,
    finished_at: datetime,
) -> None:
    """`_fail_job`, if the run still owns the job; otherwise the failure is dropped."""
    locked = await _lock_if_owned(session, fence)
    if locked is None:
        return
    job, bom = locked
    await _fail_job(
        session,
        job,
        bom,
        error_code=error_code,
        error_message=error_message,
        finished_at=finished_at,
    )


async def _record_success(
    session: AsyncSession, fence: _Fence, snapshot: _ParseSnapshot, finished_at: datetime
) -> bool:
    """If the run still owns the job: store the snapshot on the upload (`PARSED`), finish the
    job, audit it, and commit. Returns whether the result was recorded."""
    locked = await _lock_if_owned(session, fence)
    if locked is None:
        return False
    job, bom = locked
    bom.parse_options = snapshot.parse_options
    bom.detected_columns = snapshot.detected_columns
    bom.row_count = snapshot.row_count
    bom.column_count = snapshot.column_count
    bom.parse_result = snapshot.parse_result
    bom.status = UploadStatus.PARSED
    job.status = JobStatus.SUCCEEDED
    job.finished_at = finished_at
    await AuditService(session).record(
        AuditAction.BOM_PARSED,
        entity_type="bom_upload",
        entity_id=bom.id,
        actor_id=job.requested_by_id,
        workspace_id=bom.workspace_id,
        metadata={"row_count": snapshot.row_count, "column_count": snapshot.column_count},
    )
    await session.commit()
    return True


async def _requeue_owned_job(session: AsyncSession, fence: _Fence) -> bool:
    """Put the job back to `QUEUED` (storage unavailable) if the run still owns it."""
    locked = await _lock_if_owned(session, fence)
    if locked is None:
        return False
    job, _ = locked
    job.status = JobStatus.QUEUED
    job.error_code = STORAGE_UNAVAILABLE
    job.error_message = STORAGE_UNAVAILABLE_MESSAGE
    await session.commit()
    return True


async def _download_and_parse(
    storage: Storage, bom: BomUpload, options: ParseOptions, limits: ParseLimits
) -> ParseOutcome:
    """Fetch the original into a private temporary directory and parse it.

    The parse runs synchronously on the calling (event loop) thread, not in a worker thread: in
    the Celery worker that is the process's main thread, where the soft time limit raises
    `SoftTimeLimitExceeded`, so the limit interrupts the parse itself and the exception reaches
    `run_parse_job`. The worker's event loop has nothing else to do meanwhile.
    """
    with tempfile.TemporaryDirectory(prefix="bomviz-parse-", ignore_cleanup_errors=True) as tmp:
        path = Path(tmp) / f"original.{FILE_EXTENSIONS[bom.file_format]}"
        await storage.download_to(bom.original_file_key, path)
        return parse_file(
            path,
            bom.file_format.value,
            options,
            limits,
            header_scorer=count_standard_field_matches,
        )


async def run_parse_job(
    job_id: uuid.UUID,
    *,
    task_id: str,
    session_factory: async_sessionmaker[AsyncSession],
    storage: Storage,
    settings: Settings,
    now: Callable[[], datetime] = utc_now,
) -> None:
    """Run the parse job `job_id` as the Celery task `task_id`, recording its outcome.

    `session_factory` must use `expire_on_commit=False` (see `build_sessionmaker`): the job and
    upload are used again after each commit. Whether the job runs at all is decided by
    `_run_decision` (ownership and the attempt cap, see the module docstring); a job this task
    does not own, or that is missing or finished, is left untouched. Every outcome is written
    only while this run still owns the job (`_lock_if_owned`), otherwise it is dropped.
    Parse failures, the soft time limit and unexpected errors are recorded on the job and the
    upload and do not raise. Raises `RetryableJobError` when storage is temporarily unavailable
    (the job is requeued first). Database errors propagate.
    """
    async with session_factory() as session:
        locked = await _lock_job_with_upload(session, job_id)
        if locked is None:
            logger.warning("parse_job_not_found", extra={"job_id": str(job_id)})
            return
        job, bom = locked
        context = {"job_id": str(job.id), "bom_upload_id": str(bom.id)}
        decision = _run_decision(job, task_id)
        if decision is _RunDecision.SKIP:
            skipped_status = job.status.value  # Read before the rollback expires the job.
            await session.rollback()
            logger.info("parse_job_skipped", extra={**context, "job_status": skipped_status})
            return
        if decision is _RunDecision.GIVE_UP:
            logger.error(
                "parse_job_attempts_exhausted", extra={**context, "attempts": job.attempts}
            )
            await _fail_job(
                session,
                job,
                bom,
                error_code=INTERNAL_ERROR,
                error_message=INTERNAL_ERROR_MESSAGE,
                finished_at=now(),
            )
            return

        await _start_job(session, job, bom, task_id, now())
        fence = _Fence(job_id=job.id, task_id=task_id, attempt=job.attempts, context=context)
        logger.info("parse_job_started", extra={**context, "attempt": fence.attempt})

        failure: tuple[str, str]
        try:
            options = _options_from_parameters(job.parameters)
            outcome = await _download_and_parse(
                storage, bom, options, ParseLimits.from_settings(settings)
            )
            snapshot = _snapshot(outcome)
        except ParseError as exc:
            logger.info("parse_job_failed", extra={**context, "error_code": exc.code.value})
            failure = (exc.code.value, exc.message)
        except _InvalidStoredOptionsError:
            logger.warning("parse_job_invalid_options", extra=context, exc_info=True)
            failure = (INVALID_OPTIONS, INVALID_OPTIONS_MESSAGE)
        except SoftTimeLimitExceeded:
            logger.warning("parse_job_time_limit_exceeded", extra=context)
            failure = (ParseErrorCode.FILE_TOO_COMPLEX.value, TIME_LIMIT_MESSAGE)
        except StorageUnavailableError as exc:
            logger.warning("parse_job_storage_unavailable", extra=context, exc_info=True)
            if await _requeue_owned_job(session, fence):
                raise RetryableJobError(f"storage unavailable for parse job {job_id}") from exc
            return
        except StorageObjectNotFoundError:
            logger.error("parse_job_original_missing", extra=context, exc_info=True)
            failure = (INTERNAL_ERROR, INTERNAL_ERROR_MESSAGE)
        except Exception:
            logger.exception("parse_job_crashed", extra=context)
            failure = (INTERNAL_ERROR, INTERNAL_ERROR_MESSAGE)
        else:
            if await _record_success(session, fence, snapshot, now()):
                logger.info(
                    "parse_job_succeeded",
                    extra={
                        **context,
                        "row_count": snapshot.row_count,
                        "column_count": snapshot.column_count,
                    },
                )
            return

        error_code, error_message = failure
        await _fail_owned_job(
            session, fence, error_code=error_code, error_message=error_message, finished_at=now()
        )


async def mark_job_failed(
    job_id: uuid.UUID,
    error_code: str,
    error_message: str,
    *,
    task_id: str | None = None,
    session_factory: async_sessionmaker[AsyncSession],
    now: Callable[[], datetime] = utc_now,
) -> None:
    """Fail an active (`QUEUED`/`RUNNING`) job and its upload; other jobs are left untouched.

    Used by the worker when a task gives up on a job (retries exhausted, time limit). With
    `task_id`, a job owned by another task is left untouched too.
    """
    async with session_factory() as session:
        locked = await _lock_job_with_upload(session, job_id)
        if locked is None:
            logger.warning("parse_job_not_found", extra={"job_id": str(job_id)})
            return
        job, bom = locked
        owned = task_id is None or job.celery_task_id in (None, task_id)
        if job.status not in ACTIVE_JOB_STATUSES or not owned:
            await session.rollback()
            return
        await _fail_job(
            session,
            job,
            bom,
            error_code=error_code,
            error_message=error_message,
            finished_at=now(),
        )
        logger.error(
            "parse_job_marked_failed",
            extra={"job_id": str(job.id), "bom_upload_id": str(bom.id), "error_code": error_code},
        )


class ParseRequestService:
    """API side of parse jobs: queue a re-parse, read the raw preview.

    `request_parse` commits inside the service (commit-before-enqueue, as for uploads): the
    worker must see the job row (and its Celery task id) before the task is handed to the
    queue. An enqueue failure is recorded in a second transaction (`enqueue_parse_job`).
    """

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self._session = session
        self._audit = AuditService(session)
        self._settings = settings
        self._now = now

    async def request_parse(
        self,
        access: BomAccess,
        actor: User,
        parameters: dict[str, Any],
        queue: JobQueue,
    ) -> ImportJob:
        """Queue a new parse job with `parameters` (the explicitly requested options).

        An active job blocks the request (`JobInProgressError`) unless it is stale: then it is
        marked `FAILED` `STALE_JOB` and the request proceeds. Only `UPLOADED`, `PARSED` and
        `PARSE_FAILED` uploads can be parsed (`InvalidStateError`). If the job cannot be
        enqueued it is returned `FAILED` `QUEUE_UNAVAILABLE` and the upload is `PARSE_FAILED`.
        """
        bom = await _lock_upload(self._session, access.bom.id)
        if bom is None:  # Deleted between access resolution and the lock.
            raise InvalidStateError(access.bom.status)
        now = self._now()
        await self._retire_stale_job(bom, now)
        if bom.status not in REPARSE_ALLOWED_STATUSES:
            raise InvalidStateError(bom.status)

        job = ImportJob(
            bom_upload_id=bom.id,
            job_type=JobType.PARSE,
            status=JobStatus.QUEUED,
            parameters=parameters,
            requested_by_id=actor.id,
            queued_at=now,
            celery_task_id=new_task_id(),
        )
        try:
            async with self._session.begin_nested():
                self._session.add(job)
        except IntegrityError as exc:
            if _ACTIVE_JOB_INDEX not in str(exc.orig):
                raise
            raise JobInProgressError() from exc
        await self._audit.record(
            AuditAction.BOM_PARSE_REQUESTED,
            entity_type="bom_upload",
            entity_id=bom.id,
            actor_id=actor.id,
            workspace_id=bom.workspace_id,
            metadata={"job_id": str(job.id)},
        )
        await self._session.commit()

        await enqueue_parse_job(self._session, queue, job, now=self._now)
        # Loads the server-side defaults (attempts, timestamps) for the response.
        await self._session.refresh(job)
        return job

    async def raw_preview(self, access: BomAccess) -> BomUpload:
        """The upload whose `parse_result` holds the raw preview; it must be `PARSED`."""
        bom = access.bom
        if bom.status is not UploadStatus.PARSED or bom.parse_result is None:
            raise NotParsedError()
        return bom

    async def _retire_stale_job(self, bom: BomUpload, now: datetime) -> None:
        """Fail the active job if it is stale; raise `JobInProgressError` if it is not."""
        active = (
            await self._session.scalars(
                select(ImportJob)
                .where(
                    ImportJob.bom_upload_id == bom.id,
                    ImportJob.status.in_(ACTIVE_JOB_STATUSES),
                )
                .with_for_update()
            )
        ).one_or_none()
        if active is None:
            return
        since = active.started_at if active.status is JobStatus.RUNNING else None
        cutoff = now - timedelta(minutes=self._settings.job_stale_after_minutes)
        if (since or active.queued_at) > cutoff:
            raise JobInProgressError()
        logger.warning(
            "parse_job_stale",
            extra={
                "job_id": str(active.id),
                "bom_upload_id": str(bom.id),
                "job_status": active.status.value,
            },
        )
        active.status = JobStatus.FAILED
        active.error_code = STALE_JOB
        active.error_message = STALE_JOB_MESSAGE
        active.finished_at = now
        if bom.status is UploadStatus.PARSING:
            bom.status = UploadStatus.PARSE_FAILED
        await self._session.flush()
