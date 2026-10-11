"""The `boms.parse` Celery task's options and retry wiring, and the Celery-backed job queue.

No broker is involved: the task body is called with a stand-in for the bound task, and the
async job functions it runs are replaced with recording fakes.
"""

import logging
import random
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, get_args

import pytest
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy.exc import OperationalError

from app.schemas.bom import Delimiter, Encoding
from app.services.celery_job_queue import ENQUEUE_RETRY_POLICY, CeleryJobQueue
from app.services.parse_job_service import RetryableJobError
from app.services.parsing import ALLOWED_DELIMITERS, ALLOWED_ENCODINGS
from app.workers import tasks
from app.workers.celery_app import celery_app

JOB_ID = uuid.UUID("0190a5c4-0000-7000-8000-000000000001")
TASK_ID = "5c0ffee0-0000-4000-8000-000000000001"
BACKEND_DIR = Path(__file__).resolve().parents[2]


class RetryRequestedError(Exception):
    """What the fake bound task's `retry` returns (Celery's returns a `Retry` to raise)."""

    def __init__(self, exc: Exception, countdown: float) -> None:
        super().__init__("retry")
        self.exc = exc
        self.countdown = countdown


class FakeBoundTask:
    def __init__(self, retries: int) -> None:
        self.request = SimpleNamespace(retries=retries, id=TASK_ID)

    def retry(self, *, exc: Exception, countdown: float) -> RetryRequestedError:
        return RetryRequestedError(exc, countdown)


@dataclass
class JobCalls:
    run_error: Exception | None = None
    mark_error: Exception | None = None
    runs: list[tuple[uuid.UUID, str]] = field(default_factory=list)
    failed: list[tuple[uuid.UUID, str, str]] = field(default_factory=list)


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> JobCalls:
    recorded = JobCalls()

    async def run(job_id: uuid.UUID, task_id: str) -> None:
        recorded.runs.append((job_id, task_id))
        if recorded.run_error is not None:
            raise recorded.run_error

    async def mark_failed(job_id: uuid.UUID, task_id: str, error_code: str, _: str) -> None:
        if recorded.mark_error is not None:
            raise recorded.mark_error
        recorded.failed.append((job_id, task_id, error_code))

    monkeypatch.setattr(tasks, "_run_parse", run)
    monkeypatch.setattr(tasks, "_mark_failed", mark_failed)
    return recorded


def test_the_parse_task_is_registered_with_late_acks_and_three_retries() -> None:
    task = celery_app.tasks["boms.parse"]

    assert task.acks_late is True
    assert task.max_retries == 3


def test_the_parse_task_has_the_configured_soft_and_hard_time_limits() -> None:
    task = celery_app.tasks["boms.parse"]

    assert task.soft_time_limit == 600
    assert task.time_limit == 660


def test_the_celery_app_includes_the_task_module() -> None:
    assert "app.workers.tasks" in celery_app.conf.include


def test_a_successful_run_neither_retries_nor_fails_the_job(calls: JobCalls) -> None:
    tasks.parse_bom(FakeBoundTask(retries=0), str(JOB_ID))

    assert calls.runs == [(JOB_ID, TASK_ID)]
    assert calls.failed == []


@pytest.mark.parametrize(
    ("retries", "base"),
    [(0, 5), (1, 20), (2, 60)],
)
def test_a_retryable_error_schedules_a_retry_with_a_growing_countdown(
    calls: JobCalls, retries: int, base: int
) -> None:
    calls.run_error = RetryableJobError("storage down")

    with pytest.raises(RetryRequestedError) as raised:
        tasks.parse_bom(FakeBoundTask(retries=retries), str(JOB_ID))

    assert raised.value.exc is calls.run_error
    assert base <= raised.value.countdown <= base * 1.1
    assert calls.failed == []


def test_a_database_connection_error_is_retried(calls: JobCalls) -> None:
    calls.run_error = ConnectionRefusedError("connection refused")

    with pytest.raises(RetryRequestedError):
        tasks.parse_bom(FakeBoundTask(retries=0), str(JOB_ID))


def test_exhausted_storage_retries_fail_the_job_as_storage_unavailable(calls: JobCalls) -> None:
    calls.run_error = RetryableJobError("storage down")

    tasks.parse_bom(FakeBoundTask(retries=3), str(JOB_ID))

    assert calls.failed == [(JOB_ID, TASK_ID, "STORAGE_UNAVAILABLE")]


@pytest.mark.parametrize(
    "error",
    [
        OperationalError("COMMIT", {}, ConnectionResetError("connection reset")),
        ConnectionRefusedError("connection refused"),
    ],
)
def test_exhausted_database_retries_fail_the_job_as_internal_error(
    calls: JobCalls, error: Exception
) -> None:
    calls.run_error = error

    tasks.parse_bom(FakeBoundTask(retries=3), str(JOB_ID))

    assert calls.failed == [(JOB_ID, TASK_ID, "INTERNAL_ERROR")]


def test_a_failure_to_mark_the_job_failed_is_logged_at_error_and_raised(
    calls: JobCalls, caplog: pytest.LogCaptureFixture
) -> None:
    calls.run_error = RetryableJobError("storage down")
    calls.mark_error = ConnectionRefusedError("database down")

    with (
        caplog.at_level(logging.ERROR, logger=tasks.__name__),
        pytest.raises(ConnectionRefusedError),
    ):
        tasks.parse_bom(FakeBoundTask(retries=3), str(JOB_ID))

    [record] = [r for r in caplog.records if r.getMessage() == "parse_job_mark_failed_error"]
    assert record.levelno == logging.ERROR
    assert getattr(record, "job_id", None) == str(JOB_ID)
    assert getattr(record, "error_code", None) == "STORAGE_UNAVAILABLE"
    assert record.exc_info is not None


def test_a_soft_time_limit_outside_the_parse_fails_the_job_as_too_complex(
    calls: JobCalls,
) -> None:
    calls.run_error = SoftTimeLimitExceeded()

    tasks.parse_bom(FakeBoundTask(retries=0), str(JOB_ID))

    assert calls.failed == [(JOB_ID, TASK_ID, "PARSE_FILE_TOO_COMPLEX")]


def test_other_errors_are_not_retried(calls: JobCalls) -> None:
    calls.run_error = RuntimeError("bug")

    with pytest.raises(RuntimeError):
        tasks.parse_bom(FakeBoundTask(retries=0), str(JOB_ID))

    assert calls.failed == []


def test_retry_countdown_adds_at_most_ten_percent_jitter() -> None:
    rng = random.Random(7)  # noqa: S311 - seeded for a deterministic test, not security

    countdowns = [tasks.retry_countdown(2, rng) for _ in range(50)]

    assert all(60 <= value <= 66 for value in countdowns)
    assert len(set(countdowns)) > 1


class RecordingCelery:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []

    def send_task(self, name: str, **options: Any) -> SimpleNamespace:
        self.sent.append((name, options))
        return SimpleNamespace(id=options.get("task_id"))


def test_the_celery_queue_sends_the_parse_task_by_name_with_the_given_task_id() -> None:
    celery = RecordingCelery()

    CeleryJobQueue(celery).enqueue_parse(JOB_ID, TASK_ID)

    [(name, options)] = celery.sent
    assert name == "boms.parse"
    assert options["args"] == [str(JOB_ID)]
    assert options["task_id"] == TASK_ID
    assert options["retry_policy"] == ENQUEUE_RETRY_POLICY


def test_importing_the_api_does_not_import_the_worker_entry_point() -> None:
    probe = "import sys, app.main; print('app.workers.celery_app' in sys.modules)"

    result = subprocess.run(  # noqa: S603 - fixed interpreter and arguments
        [sys.executable, "-c", probe],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )

    assert result.stdout.strip() == "False"


def test_the_parse_request_allowlists_match_the_parser() -> None:
    assert set(get_args(Delimiter)) == set(ALLOWED_DELIMITERS)
    assert set(get_args(Encoding)) == set(ALLOWED_ENCODINGS)
