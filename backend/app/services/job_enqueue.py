"""Handing a committed parse job to the queue: shared by uploads and re-parse requests.

The API owns the Celery task id: `new_task_id()` is stored on the job (`celery_task_id`) in the
transaction that creates it, and the task is sent with that id, so the worker can recognise its
own job on a retry or redelivery and ignore jobs it does not own.

A failed publish does not prove the task was not delivered (the broker may have accepted it
before the error reached us, and a worker may already be running it). The failure is therefore
only recorded if the job is still `QUEUED` under the same task id: the job becomes `FAILED`
`QUEUE_UNAVAILABLE` and its upload `PARSE_FAILED`. A still-`QUEUED` job is the upload's only
active job (a partial unique index allows one), so the upload state is still this job's to set.
Locks are taken upload first, then job, as everywhere else.
"""

import asyncio
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BomUpload, ImportJob, JobStatus, UploadStatus
from app.services.job_queue import JobQueue

logger = logging.getLogger(__name__)

QUEUE_UNAVAILABLE = "QUEUE_UNAVAILABLE"
QUEUE_UNAVAILABLE_MESSAGE = "The file could not be queued for parsing. Start parsing it again."


def new_task_id() -> str:
    """A fresh Celery task id for a parse job."""
    return str(uuid.uuid4())


def _utc_now() -> datetime:
    return datetime.now(UTC)


async def enqueue_parse_job(
    session: AsyncSession,
    queue: JobQueue,
    job: ImportJob,
    *,
    now: Callable[[], datetime] = _utc_now,
) -> None:
    """Hand the committed `job` to `queue`; on failure, fail it if it is still `QUEUED`.

    `job.celery_task_id` must already be committed. Never raises for a queue failure (it is
    logged and recorded on the job); database errors propagate.
    """
    task_id = job.celery_task_id
    if task_id is None:
        raise ValueError("a parse job needs its celery_task_id before it is enqueued")
    context = {"job_id": str(job.id), "bom_upload_id": str(job.bom_upload_id)}
    try:
        await asyncio.to_thread(queue.enqueue_parse, job.id, task_id)
    except Exception:
        logger.warning("parse_enqueue_failed", extra=context, exc_info=True)
        await _fail_if_still_queued(session, job, task_id, now())


async def _fail_if_still_queued(
    session: AsyncSession, job: ImportJob, task_id: str, finished_at: datetime
) -> None:
    bom = (
        await session.scalars(
            select(BomUpload)
            .where(BomUpload.id == job.bom_upload_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).one_or_none()
    locked_job = (
        await session.scalars(
            select(ImportJob)
            .where(ImportJob.id == job.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).one_or_none()
    if (
        locked_job is None
        or locked_job.status is not JobStatus.QUEUED
        or locked_job.celery_task_id != task_id
    ):
        # Already picked up (or replaced) despite the error: the worker owns its outcome.
        logger.warning(
            "parse_enqueue_failure_ignored",
            extra={
                "job_id": str(job.id),
                "bom_upload_id": str(job.bom_upload_id),
                "job_status": locked_job.status.value if locked_job is not None else None,
            },
        )
        await session.commit()
        return
    locked_job.status = JobStatus.FAILED
    locked_job.error_code = QUEUE_UNAVAILABLE
    locked_job.error_message = QUEUE_UNAVAILABLE_MESSAGE
    locked_job.finished_at = finished_at
    if bom is not None:
        bom.status = UploadStatus.PARSE_FAILED
    await session.commit()
