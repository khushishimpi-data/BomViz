"""The Celery-backed `JobQueue`.

Tasks are sent by name, so the API process never imports the worker's task modules. The task id
is chosen by the caller and stored on the job beforehand, so the worker can tell whether it owns
the job it is given (see `run_parse_job`). Publishing is retried briefly (bounded, with a short
backoff) and then fails fast: the caller records the job as `FAILED` `QUEUE_UNAVAILABLE` rather
than hanging the request on an unreachable broker.
"""

import uuid
from typing import Any

PARSE_TASK_NAME = "boms.parse"

# Kombu publish retry policy: at most 2 retries, 0.2 s then 0.4 s apart (well under a second).
ENQUEUE_RETRY_POLICY: dict[str, float] = {
    "max_retries": 2,
    "interval_start": 0.2,
    "interval_step": 0.2,
    "interval_max": 0.5,
}


class CeleryJobQueue:
    """Hands jobs to Celery workers through the configured broker."""

    def __init__(self, celery: Any) -> None:
        self._celery = celery

    def enqueue_parse(self, job_id: uuid.UUID, task_id: str) -> None:
        self._celery.send_task(
            PARSE_TASK_NAME,
            args=[str(job_id)],
            task_id=task_id,
            retry=True,
            retry_policy=ENQUEUE_RETRY_POLICY,
        )
