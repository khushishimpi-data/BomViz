"""Celery tasks. Each task runs the same async services as the API through `asyncio.run`.

`boms.parse` is acknowledged late, so the broker may deliver it again after a worker crash; any
such copy keeps the task id and is handled by the ownership rule below. The task id is chosen by the
API and stored on the job; the task passes its own id to `run_parse_job`, which only runs a job
the task owns and caps the runs of one job (see `parse_job_service`). Parse errors are recorded
on the job and never retried. Only transient infrastructure failures are retried: unavailable
object storage (`RetryableJobError`, the job is already back to `QUEUED`) and database
connection errors (the job may be left `RUNNING`; the retry, which keeps the task id, resumes
it). Retries are bounded (`MAX_RETRIES`) with growing countdowns of about 5, 20 and 60 seconds
plus up to 10% jitter; after the last one the job is marked `FAILED`: `STORAGE_UNAVAILABLE`
for storage, `INTERNAL_ERROR` for the database. If even that write fails, it is logged at error
level and the task fails (the job stays active until the stale-job rule replaces it).

Time limits: the soft limit (`PARSE_SOFT_TIME_LIMIT_SECONDS`) normally interrupts the parse
inside `run_parse_job`, which fails the job `PARSE_FILE_TOO_COMPLEX`. If it fires elsewhere
(downloading, database I/O) the exception leaves `asyncio.run` and the task marks the job failed
with the same code. The hard limit (`PARSE_HARD_TIME_LIMIT_SECONDS`) kills the worker process;
if the broker delivers the task again, that copy resumes the job within the attempt cap;
otherwise the stale-job rule (longer
than the soft limit, enforced by the settings) lets a new parse replace it.

Each run builds its own engine with `NullPool` (no connection outlives the event loop that
`asyncio.run` creates) and its own object storage client, and disposes the engine afterwards.
"""

import asyncio
import logging
import random
import uuid
from typing import Any

from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.db.session import build_sessionmaker
from app.services.celery_job_queue import PARSE_TASK_NAME
from app.services.parse_job_service import (
    INTERNAL_ERROR,
    INTERNAL_ERROR_MESSAGE,
    STORAGE_UNAVAILABLE,
    STORAGE_UNAVAILABLE_MESSAGE,
    TIME_LIMIT_MESSAGE,
    RetryableJobError,
    mark_job_failed,
    run_parse_job,
)
from app.services.parsing import ParseErrorCode
from app.services.storage_service import ObjectStorage
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_COUNTDOWNS_SECONDS = (5, 20, 60)
RETRY_JITTER_RATIO = 0.1
RETRYABLE_ERRORS: tuple[type[Exception], ...] = (
    RetryableJobError,
    OperationalError,
    InterfaceError,
    ConnectionError,
)


def retry_countdown(retries: int, rng: random.Random | None = None) -> float:
    """Seconds before retry number `retries + 1`: the base countdown plus up to 10% jitter."""
    base = RETRY_COUNTDOWNS_SECONDS[min(retries, len(RETRY_COUNTDOWNS_SECONDS) - 1)]
    jitter = (rng or random).uniform(0, base * RETRY_JITTER_RATIO)
    return base + jitter


async def _run_parse(job_id: uuid.UUID, task_id: str) -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        await run_parse_job(
            job_id,
            task_id=task_id,
            session_factory=build_sessionmaker(engine),
            storage=ObjectStorage(settings),
            settings=settings,
        )
    finally:
        await engine.dispose()


async def _mark_failed(
    job_id: uuid.UUID, task_id: str, error_code: str, error_message: str
) -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        await mark_job_failed(
            job_id,
            error_code,
            error_message,
            task_id=task_id,
            session_factory=build_sessionmaker(engine),
        )
    finally:
        await engine.dispose()


def _give_up(job_id: uuid.UUID, task_id: str, error_code: str, error_message: str) -> None:
    """Mark the job failed; if that write fails too, log it at error level and re-raise."""
    try:
        asyncio.run(_mark_failed(job_id, task_id, error_code, error_message))
    except Exception:
        logger.exception(
            "parse_job_mark_failed_error",
            extra={"job_id": str(job_id), "task_id": task_id, "error_code": error_code},
        )
        raise


def _exhausted_failure(exc: Exception) -> tuple[str, str]:
    """The job error recorded when retries for `exc` run out."""
    if isinstance(exc, RetryableJobError):
        return STORAGE_UNAVAILABLE, STORAGE_UNAVAILABLE_MESSAGE
    return INTERNAL_ERROR, INTERNAL_ERROR_MESSAGE


def parse_bom(task: Any, job_id: str) -> None:
    """Body of `boms.parse` (`task` is the bound Celery task)."""
    parsed_id = uuid.UUID(job_id)
    task_id = str(task.request.id)
    try:
        asyncio.run(_run_parse(parsed_id, task_id))
    except SoftTimeLimitExceeded:
        logger.warning(
            "parse_job_time_limit_exceeded_outside_parse",
            extra={"job_id": job_id, "task_id": task_id},
        )
        _give_up(parsed_id, task_id, ParseErrorCode.FILE_TOO_COMPLEX.value, TIME_LIMIT_MESSAGE)
    except RETRYABLE_ERRORS as exc:
        retries: int = task.request.retries
        if retries >= MAX_RETRIES:
            _give_up(parsed_id, task_id, *_exhausted_failure(exc))
            return
        countdown = retry_countdown(retries)
        logger.warning(
            "parse_job_retry_scheduled",
            extra={"job_id": job_id, "retry": retries + 1, "countdown_seconds": countdown},
        )
        raise task.retry(exc=exc, countdown=countdown) from exc


_settings = get_settings()

# Registered without decorator syntax: Celery is untyped, and mypy rejects untyped decorators.
parse_bom_task = celery_app.task(
    name=PARSE_TASK_NAME,
    bind=True,
    acks_late=True,
    max_retries=MAX_RETRIES,
    soft_time_limit=_settings.parse_soft_time_limit_seconds,
    time_limit=_settings.parse_hard_time_limit_seconds,
)(parse_bom)
