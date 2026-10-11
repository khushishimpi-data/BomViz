"""The queue that background jobs are handed to, behind an injectable protocol.

Services depend on `JobQueue`, never on Celery directly. The application lifespan sets
`app.state.job_queue` to the Celery-backed `CeleryJobQueue`. When it is None (not configured),
`get_job_queue` returns `UnavailableJobQueue`, whose every enqueue fails: an upload still
succeeds and its parse job is marked `FAILED` `QUEUE_UNAVAILABLE`, exactly as when the broker is
down.
"""

import uuid
from typing import Protocol

from fastapi import Request


class QueueUnavailableError(Exception):
    """The job could not be handed to the queue. Internal: never shown to clients as is."""


class JobQueue(Protocol):
    """Hands jobs to background workers."""

    def enqueue_parse(self, job_id: uuid.UUID, task_id: str) -> None:
        """Queue the parse job `job_id` as the task `task_id` (the id stored on the job).

        Raises on failure. An exception does not prove the job was not queued (the broker may
        have accepted it before the error), so callers only fail a job that is still `QUEUED`.
        May block on network I/O, so async callers run it in a worker thread.
        """
        ...


class UnavailableJobQueue:
    """Stand-in used while no queue is configured: every enqueue fails."""

    def enqueue_parse(self, job_id: uuid.UUID, task_id: str) -> None:
        raise QueueUnavailableError("No job queue is configured.")


def get_job_queue(request: Request) -> JobQueue:
    """The application's job queue, or `UnavailableJobQueue` when none is configured."""
    queue: JobQueue | None = getattr(request.app.state, "job_queue", None)
    return queue if queue is not None else UnavailableJobQueue()
