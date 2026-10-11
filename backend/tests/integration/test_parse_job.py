"""The parse job runner (`run_parse_job`, `mark_job_failed`) against a real PostgreSQL.

Jobs are run directly (no Celery) with a `FakeStorage` holding real small files. Covers the
success snapshot for CSV, XLSX and XLS, explicit options overriding detection, every failure
path's codes, statuses and audit rows, the retryable storage failure, the soft time limit, task
ownership (retries and redeliveries of the owning task, the attempt cap, other tasks' jobs) and
fencing of late results from a run that no longer owns its job.
"""

import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from celery.exceptions import SoftTimeLimitExceeded
from openpyxl import Workbook
from sqlalchemy import event, inspect, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.core.config import Settings
from app.db.session import build_sessionmaker
from app.models import (
    AuditLog,
    BomUpload,
    FileFormat,
    ImportJob,
    JobStatus,
    JobType,
    UploadStatus,
    User,
    Workspace,
)
from app.services import parse_job_service
from app.services.parse_job_service import RetryableJobError, mark_job_failed, run_parse_job
from tests.conftest import make_settings
from tests.fakes import FakeStorage, StoredObject

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "boms"
NOW = datetime(2026, 10, 11, 8, 30, tzinfo=UTC)
TASK_ID = "5c0ffee0-0000-4000-8000-000000000001"
OTHER_TASK_ID = "5c0ffee0-0000-4000-8000-000000000002"
CSV_BOM = b"Part Number,Description,Qty\nP-1,Bolt,4\n,,\nP-2,Nut,2\n"
CONTENT_TYPES = {
    FileFormat.CSV: "text/csv",
    FileFormat.XLSX: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    FileFormat.XLS: "application/vnd.ms-excel",
}


def _now() -> datetime:
    return NOW


@dataclass
class Env:
    session: AsyncSession
    engine: AsyncEngine
    factory: async_sessionmaker[AsyncSession]
    storage: FakeStorage
    settings: Settings
    user_id: uuid.UUID
    workspace_id: uuid.UUID


@pytest.fixture
async def env(db_session: AsyncSession, migrated_database_url: str) -> AsyncIterator[Env]:
    user = User(email="engineer@example.com", password_hash="not-a-real-hash", full_name="Eng")
    db_session.add(user)
    await db_session.flush()
    workspace = Workspace(name="Acme", slug="acme", owner_id=user.id)
    db_session.add(workspace)
    await db_session.commit()
    engine = create_async_engine(migrated_database_url, poolclass=NullPool)
    try:
        yield Env(
            session=db_session,
            engine=engine,
            factory=build_sessionmaker(engine),
            storage=FakeStorage(),
            settings=make_settings(database_url=migrated_database_url),
            user_id=user.id,
            workspace_id=workspace.id,
        )
    finally:
        await engine.dispose()


async def _queued_job(
    env: Env,
    data: bytes,
    file_format: FileFormat = FileFormat.CSV,
    parameters: dict[str, Any] | None = None,
    *,
    status: JobStatus = JobStatus.QUEUED,
    store: bool = True,
    task_id: str | None = None,
    attempts: int = 0,
) -> ImportJob:
    """An upload of `data` (stored in the fake storage unless `store=False`) with one job owned
    by `task_id` (None: the job predates stored task ids)."""
    bom_id = uuid.uuid4()
    key = f"workspaces/{env.workspace_id}/boms/{bom_id}/original.{file_format.value.lower()}"
    env.session.add(
        BomUpload(
            id=bom_id,
            workspace_id=env.workspace_id,
            bom_name="Main BOM",
            original_filename=f"bom.{file_format.value.lower()}",
            file_format=file_format,
            content_type=CONTENT_TYPES[file_format],
            size_bytes=len(data),
            checksum_sha256=uuid.uuid4().hex * 2,
            original_file_key=key,
            status=UploadStatus.UPLOADED,
            uploaded_by_id=env.user_id,
        )
    )
    await env.session.flush()
    job = ImportJob(
        bom_upload_id=bom_id,
        job_type=JobType.PARSE,
        status=status,
        parameters=parameters or {},
        requested_by_id=env.user_id,
        celery_task_id=task_id,
        attempts=attempts,
    )
    env.session.add(job)
    await env.session.commit()
    if store:
        env.storage.objects[key] = StoredObject(data, CONTENT_TYPES[file_format])
    return job


async def _run(
    env: Env,
    job: ImportJob,
    settings: Settings | None = None,
    *,
    task_id: str = TASK_ID,
    factory: async_sessionmaker[AsyncSession] | None = None,
    storage: FakeStorage | None = None,
) -> None:
    await run_parse_job(
        job.id,
        task_id=task_id,
        session_factory=factory or env.factory,
        storage=storage or env.storage,
        settings=settings or env.settings,
        now=_now,
    )


async def _reload(env: Env, job: ImportJob) -> tuple[ImportJob, BomUpload]:
    """Fresh copies of the job and its upload (the identity key needs no database round trip)."""
    identity = inspect(job).identity
    assert identity is not None
    job_id = identity[0]
    env.session.expire_all()
    fresh_job = await env.session.get(ImportJob, job_id, populate_existing=True)
    assert fresh_job is not None
    bom = await env.session.get(BomUpload, fresh_job.bom_upload_id, populate_existing=True)
    assert bom is not None
    return fresh_job, bom


async def _audits(env: Env) -> list[AuditLog]:
    env.session.expire_all()
    return list((await env.session.scalars(select(AuditLog).order_by(AuditLog.created_at))).all())


def _xlsx_bytes(tmp_path: Path) -> bytes:
    workbook = Workbook()
    notes = workbook.active
    assert notes is not None
    notes.title = "Notes"
    parts = workbook.create_sheet("Parts")
    parts.append(["Part Number", "Part Name", "Quantity"])
    parts.append(["P-1", "Bolt", 4])
    parts.append(["P-2", "Nut", 2.5])
    path = tmp_path / "bom.xlsx"
    workbook.save(path)
    return path.read_bytes()


# Success


async def test_a_csv_job_stores_the_preview_snapshot(env: Env) -> None:
    job = await _queued_job(env, CSV_BOM)

    await _run(env, job)

    job, bom = await _reload(env, job)
    assert job.status is JobStatus.SUCCEEDED
    assert job.attempts == 1
    assert job.started_at == NOW
    assert job.finished_at == NOW
    assert job.error_code is None
    assert bom.status is UploadStatus.PARSED
    assert bom.parse_options == {
        "sheet_name": None,
        "header_row": 1,
        "delimiter": ",",
        "encoding": "utf-8",
    }
    columns = [
        {"index": 0, "name": "Part Number"},
        {"index": 1, "name": "Description"},
        {"index": 2, "name": "Qty"},
    ]
    assert bom.detected_columns == columns
    assert bom.row_count == 2
    assert bom.column_count == 3
    assert bom.parse_result == {
        "sheets": [],
        "columns": columns,
        "row_count": 2,
        "column_count": 3,
        "skipped_empty_rows": 1,
        "rows": [
            {"row_number": 2, "cells": ["P-1", "Bolt", "4"]},
            {"row_number": 4, "cells": ["P-2", "Nut", "2"]},
        ],
        "suggested_mapping": [
            {
                "source_column_index": 0,
                "source_column_name": "Part Number",
                "target_field": "part_number",
                "confidence": 1.0,
            },
            {
                "source_column_index": 1,
                "source_column_name": "Description",
                "target_field": "description",
                "confidence": 1.0,
            },
            {
                "source_column_index": 2,
                "source_column_name": "Qty",
                "target_field": "quantity",
                "confidence": 1.0,
            },
        ],
        "missing_required": ["part_name"],
    }


async def test_a_successful_job_is_audited_for_the_requester(env: Env) -> None:
    job = await _queued_job(env, CSV_BOM)
    bom_id = job.bom_upload_id

    await _run(env, job)

    [audit] = await _audits(env)
    assert audit.action == "BOM_PARSED"
    assert audit.entity_id == bom_id
    assert audit.actor_id == env.user_id
    assert audit.workspace_id == env.workspace_id
    assert audit.action_metadata == {"row_count": 2, "column_count": 3}


async def test_an_xlsx_job_picks_the_first_sheet_with_data(env: Env, tmp_path: Path) -> None:
    job = await _queued_job(env, _xlsx_bytes(tmp_path), FileFormat.XLSX)

    await _run(env, job)

    _, bom = await _reload(env, job)
    assert bom.status is UploadStatus.PARSED
    assert bom.parse_options == {
        "sheet_name": "Parts",
        "header_row": 1,
        "delimiter": None,
        "encoding": None,
    }
    assert bom.parse_result is not None
    assert bom.parse_result["sheets"] == [
        {"name": "Notes", "visible": True},
        {"name": "Parts", "visible": True},
    ]
    assert bom.parse_result["rows"] == [
        {"row_number": 2, "cells": ["P-1", "Bolt", "4"]},
        {"row_number": 3, "cells": ["P-2", "Nut", "2.5"]},
    ]
    assert bom.parse_result["missing_required"] == []


async def test_an_xls_job_detects_the_header_below_a_title(env: Env) -> None:
    job = await _queued_job(env, (FIXTURES / "basic.xls").read_bytes(), FileFormat.XLS)

    await _run(env, job)

    _, bom = await _reload(env, job)
    assert bom.status is UploadStatus.PARSED
    assert bom.parse_options is not None
    assert bom.parse_options["header_row"] == 3
    assert bom.row_count == 2
    assert bom.column_count == 6
    assert bom.parse_result is not None
    assert bom.parse_result["rows"][1] == {
        "row_number": 5,
        "cells": ["P-002", "Nut", "2.5", "1234.5", "FALSE", "2026-10-11T08:30:00"],
    }


# Explicit options


async def test_an_explicit_delimiter_and_header_row_override_detection(env: Env) -> None:
    content = b"Part Number;Qty\nP-1;2\n"
    detected = await _queued_job(env, content)
    explicit = await _queued_job(
        env, content + b"\n", parameters={"delimiter": ",", "header_row": 1}
    )

    await _run(env, detected)
    await _run(env, explicit)

    _, detected_bom = await _reload(env, detected)
    assert detected_bom.parse_options is not None
    assert detected_bom.parse_options["delimiter"] == ";"
    assert detected_bom.column_count == 2
    _, explicit_bom = await _reload(env, explicit)
    assert explicit_bom.parse_options is not None
    assert explicit_bom.parse_options["delimiter"] == ","
    assert explicit_bom.detected_columns == [{"index": 0, "name": "Part Number;Qty"}]


async def test_an_explicit_sheet_overrides_the_default_sheet(env: Env) -> None:
    content = (FIXTURES / "multi_sheet.xls").read_bytes()
    default = await _queued_job(env, content, FileFormat.XLS)
    chosen = await _queued_job(env, content, FileFormat.XLS, {"sheet_name": "Secret"})

    await _run(env, default)
    await _run(env, chosen)

    _, default_bom = await _reload(env, default)
    assert default_bom.parse_options is not None
    assert default_bom.parse_options["sheet_name"] == "Parts"
    _, chosen_bom = await _reload(env, chosen)
    assert chosen_bom.parse_options is not None
    assert chosen_bom.parse_options["sheet_name"] == "Secret"
    assert chosen_bom.detected_columns == [
        {"index": 0, "name": "Hidden A"},
        {"index": 1, "name": "Hidden B"},
    ]


# Failures


async def test_a_parse_error_fails_the_job_with_its_code(env: Env) -> None:
    job = await _queued_job(env, CSV_BOM)
    settings = make_settings(database_url=env.settings.database_url, parse_max_columns=2)

    await _run(env, job, settings)

    job, bom = await _reload(env, job)
    assert job.status is JobStatus.FAILED
    assert job.error_code == "PARSE_TOO_MANY_COLUMNS"
    assert job.error_message
    assert job.finished_at == NOW
    assert bom.status is UploadStatus.PARSE_FAILED
    assert bom.parse_result is None
    [audit] = await _audits(env)
    assert audit.action == "BOM_PARSE_FAILED"
    assert audit.actor_id == env.user_id
    assert audit.action_metadata == {"error_code": "PARSE_TOO_MANY_COLUMNS"}


async def test_an_unknown_sheet_fails_the_job(env: Env) -> None:
    content = (FIXTURES / "multi_sheet.xls").read_bytes()
    job = await _queued_job(env, content, FileFormat.XLS, {"sheet_name": "Missing"})

    await _run(env, job)

    job, _ = await _reload(env, job)
    assert job.error_code == "PARSE_SHEET_NOT_FOUND"


async def test_invalid_stored_options_fail_the_job(env: Env) -> None:
    job = await _queued_job(env, CSV_BOM, parameters={"delimiter": "x"})

    await _run(env, job)

    job, bom = await _reload(env, job)
    assert job.status is JobStatus.FAILED
    assert job.error_code == "PARSE_INVALID_OPTIONS"
    assert bom.status is UploadStatus.PARSE_FAILED


async def test_unavailable_storage_requeues_the_job_and_raises_retryable(env: Env) -> None:
    job = await _queued_job(env, CSV_BOM)
    env.storage.failing_operations.add("download_to")

    with pytest.raises(RetryableJobError):
        await _run(env, job)

    job, bom = await _reload(env, job)
    assert job.status is JobStatus.QUEUED
    assert job.error_code == "STORAGE_UNAVAILABLE"
    assert job.attempts == 1
    assert job.finished_at is None
    assert bom.status is UploadStatus.PARSING
    assert await _audits(env) == []


async def test_a_requeued_job_succeeds_once_storage_is_back(env: Env) -> None:
    job = await _queued_job(env, CSV_BOM)
    env.storage.failing_operations.add("download_to")
    with pytest.raises(RetryableJobError):
        await _run(env, job)
    env.storage.failing_operations.clear()

    await _run(env, job)

    job, bom = await _reload(env, job)
    assert job.status is JobStatus.SUCCEEDED
    assert job.attempts == 2
    assert job.error_code is None
    assert job.error_message is None
    assert bom.status is UploadStatus.PARSED


async def test_a_missing_original_fails_the_job_as_internal_error(env: Env) -> None:
    job = await _queued_job(env, CSV_BOM, store=False)

    await _run(env, job)

    job, bom = await _reload(env, job)
    assert job.status is JobStatus.FAILED
    assert job.error_code == "INTERNAL_ERROR"
    assert bom.status is UploadStatus.PARSE_FAILED


async def test_an_unexpected_error_fails_the_job_and_logs_details(
    env: Env, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    job = await _queued_job(env, CSV_BOM)

    def explode(*_: object, **__: object) -> None:
        raise RuntimeError("library internals at /tmp/secret/path")

    monkeypatch.setattr(parse_job_service, "parse_file", explode)

    with caplog.at_level(logging.ERROR, logger=parse_job_service.__name__):
        await _run(env, job)

    job, bom = await _reload(env, job)
    assert job.status is JobStatus.FAILED
    assert job.error_code == "INTERNAL_ERROR"
    assert job.error_message == "The file could not be parsed."
    assert bom.status is UploadStatus.PARSE_FAILED
    [record] = [r for r in caplog.records if r.getMessage() == "parse_job_crashed"]
    assert getattr(record, "job_id", None) == str(job.id)
    assert getattr(record, "bom_upload_id", None) == str(bom.id)
    assert record.exc_info is not None
    [audit] = await _audits(env)
    assert audit.action_metadata == {"error_code": "INTERNAL_ERROR"}


# Time limit


async def test_the_soft_time_limit_fails_the_job_as_too_complex(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = await _queued_job(env, CSV_BOM)

    def time_out(*_: object, **__: object) -> None:
        raise SoftTimeLimitExceeded()

    monkeypatch.setattr(parse_job_service, "parse_file", time_out)

    await _run(env, job)

    job, bom = await _reload(env, job)
    assert job.status is JobStatus.FAILED
    assert job.error_code == "PARSE_FILE_TOO_COMPLEX"
    assert job.error_message == "The file took too long to parse."
    assert bom.status is UploadStatus.PARSE_FAILED
    [audit] = await _audits(env)
    assert audit.action_metadata == {"error_code": "PARSE_FILE_TOO_COMPLEX"}


# Ownership


async def test_a_job_queued_without_a_task_id_is_claimed_by_the_task_that_runs_it(
    env: Env,
) -> None:
    job = await _queued_job(env, CSV_BOM)

    await _run(env, job, task_id=TASK_ID)

    job, _ = await _reload(env, job)
    assert job.status is JobStatus.SUCCEEDED
    assert job.celery_task_id == TASK_ID


def _failing_success_commits(env: Env, failures: int) -> async_sessionmaker[AsyncSession]:
    """Sessions whose first `failures` commits of a `SUCCEEDED` job fail as if the database
    connection dropped (nothing is written)."""

    class FlakySession(Session):
        pass

    remaining = [failures]

    def note_success(session: Session, _: object) -> None:
        # `dirty` still shows the pre-flush state here; the commit may come after the flush.
        if any(
            isinstance(obj, ImportJob) and obj.status is JobStatus.SUCCEEDED
            for obj in session.dirty
        ):
            session.info["succeeding"] = True

    def fail_success_commit(session: Session) -> None:
        if session.info.pop("succeeding", False) and remaining[0] > 0:
            remaining[0] -= 1
            raise OperationalError("COMMIT", {}, ConnectionResetError("connection reset"))

    event.listen(FlakySession, "after_flush", note_success)
    event.listen(FlakySession, "before_commit", fail_success_commit)
    return async_sessionmaker(env.engine, expire_on_commit=False, sync_session_class=FlakySession)


async def test_a_retry_after_a_failed_success_commit_finishes_the_job(env: Env) -> None:
    job = await _queued_job(env, CSV_BOM, task_id=TASK_ID)
    with pytest.raises(OperationalError):
        await _run(env, job, factory=_failing_success_commits(env, failures=1))
    stuck, _ = await _reload(env, job)
    assert stuck.status is JobStatus.RUNNING

    await _run(env, job)  # The Celery retry keeps the task id.

    job, bom = await _reload(env, job)
    assert job.status is JobStatus.SUCCEEDED
    assert job.attempts == 2
    assert bom.status is UploadStatus.PARSED
    [audit] = await _audits(env)
    assert audit.action == "BOM_PARSED"


async def test_a_running_job_of_the_same_task_is_failed_once_its_attempts_are_used_up(
    env: Env,
) -> None:
    job = await _queued_job(env, CSV_BOM, status=JobStatus.RUNNING, task_id=TASK_ID, attempts=3)

    await _run(env, job)

    job, bom = await _reload(env, job)
    assert job.status is JobStatus.FAILED
    assert job.error_code == "INTERNAL_ERROR"
    assert job.error_message == "The file could not be parsed."
    assert job.attempts == 3
    assert job.finished_at == NOW
    assert bom.status is UploadStatus.PARSE_FAILED
    [audit] = await _audits(env)
    assert audit.action == "BOM_PARSE_FAILED"
    assert audit.action_metadata == {"error_code": "INTERNAL_ERROR"}


@pytest.mark.parametrize("status", [JobStatus.QUEUED, JobStatus.RUNNING])
async def test_an_active_job_of_another_task_is_left_untouched(env: Env, status: JobStatus) -> None:
    job = await _queued_job(env, CSV_BOM, status=status, task_id=OTHER_TASK_ID)

    await _run(env, job, task_id=TASK_ID)

    job, bom = await _reload(env, job)
    assert job.status is status
    assert job.attempts == 0
    assert bom.status is UploadStatus.UPLOADED
    assert await _audits(env) == []


@pytest.mark.parametrize("status", [JobStatus.SUCCEEDED, JobStatus.FAILED])
async def test_a_finished_job_is_left_untouched(env: Env, status: JobStatus) -> None:
    job = await _queued_job(env, CSV_BOM, status=status, task_id=TASK_ID)

    await _run(env, job)

    job, bom = await _reload(env, job)
    assert job.status is status
    assert job.attempts == 0
    assert bom.status is UploadStatus.UPLOADED
    assert await _audits(env) == []


async def test_an_unknown_job_is_ignored(env: Env) -> None:
    await run_parse_job(
        uuid.uuid4(),
        task_id=TASK_ID,
        session_factory=env.factory,
        storage=env.storage,
        settings=env.settings,
    )

    assert await _audits(env) == []


# Fencing


class _InterruptedStorage(FakeStorage):
    """Runs `interruption` while the original is being downloaded, i.e. mid-run."""

    def __init__(self, source: FakeStorage, interruption: Callable[[], Awaitable[None]]) -> None:
        super().__init__()
        self.objects = source.objects
        self._interruption = interruption

    async def download_to(self, key: str, path: Path) -> None:
        await self._interruption()
        await super().download_to(key, path)


REPLACEMENT_RESULT = {"from": "the replacement job"}


async def _replace_as_stale(env: Env, job_id: uuid.UUID, bom_id: uuid.UUID) -> None:
    """What a re-parse request plus its successful replacement job do to a stale job."""
    await env.session.execute(
        update(ImportJob)
        .where(ImportJob.id == job_id)
        .values(status=JobStatus.FAILED, error_code="STALE_JOB", finished_at=NOW)
    )
    await env.session.execute(
        update(BomUpload)
        .where(BomUpload.id == bom_id)
        .values(status=UploadStatus.PARSED, parse_result=REPLACEMENT_RESULT, row_count=99)
    )
    await env.session.commit()


@pytest.mark.parametrize("max_columns", [200, 2], ids=["late-success", "late-failure"])
async def test_a_stale_replaced_job_finishing_late_does_not_overwrite_the_replacement(
    env: Env, caplog: pytest.LogCaptureFixture, max_columns: int
) -> None:
    job = await _queued_job(env, CSV_BOM, task_id=TASK_ID)
    job_id, bom_id = job.id, job.bom_upload_id

    async def replace() -> None:
        await _replace_as_stale(env, job_id, bom_id)

    storage = _InterruptedStorage(env.storage, replace)
    settings = make_settings(database_url=env.settings.database_url, parse_max_columns=max_columns)

    with caplog.at_level(logging.WARNING, logger=parse_job_service.__name__):
        await _run(env, job, settings, storage=storage)

    job, bom = await _reload(env, job)
    assert job.status is JobStatus.FAILED
    assert job.error_code == "STALE_JOB"
    assert bom.status is UploadStatus.PARSED
    assert bom.parse_result == REPLACEMENT_RESULT
    assert bom.row_count == 99
    assert await _audits(env) == []
    [record] = [r for r in caplog.records if r.getMessage() == "parse_job_result_discarded"]
    assert record.levelno == logging.WARNING
    assert getattr(record, "job_id", None) == str(job_id)
    assert getattr(record, "bom_upload_id", None) == str(bom_id)


async def test_a_run_overtaken_by_a_redelivered_copy_drops_its_result(env: Env) -> None:
    job = await _queued_job(env, CSV_BOM, task_id=TASK_ID)
    job_id = job.id

    async def redelivered_copy_starts() -> None:
        await env.session.execute(
            update(ImportJob).where(ImportJob.id == job_id).values(attempts=ImportJob.attempts + 1)
        )
        await env.session.commit()

    storage = _InterruptedStorage(env.storage, redelivered_copy_starts)

    await _run(env, job, storage=storage)

    job, bom = await _reload(env, job)
    assert job.status is JobStatus.RUNNING
    assert job.attempts == 2
    assert bom.parse_result is None
    assert await _audits(env) == []


# mark_job_failed (retries exhausted)


@pytest.mark.parametrize("status", [JobStatus.QUEUED, JobStatus.RUNNING])
async def test_mark_job_failed_fails_an_active_job(env: Env, status: JobStatus) -> None:
    job = await _queued_job(env, CSV_BOM, status=status)

    await mark_job_failed(
        job.id, "STORAGE_UNAVAILABLE", "Storage down.", session_factory=env.factory, now=_now
    )

    job, bom = await _reload(env, job)
    assert job.status is JobStatus.FAILED
    assert job.error_code == "STORAGE_UNAVAILABLE"
    assert job.finished_at == NOW
    assert bom.status is UploadStatus.PARSE_FAILED
    [audit] = await _audits(env)
    assert audit.action_metadata == {"error_code": "STORAGE_UNAVAILABLE"}


async def test_mark_job_failed_leaves_a_job_of_another_task_alone(env: Env) -> None:
    job = await _queued_job(env, CSV_BOM, status=JobStatus.RUNNING, task_id=OTHER_TASK_ID)

    await mark_job_failed(
        job.id,
        "STORAGE_UNAVAILABLE",
        "Storage down.",
        task_id=TASK_ID,
        session_factory=env.factory,
    )

    job, _ = await _reload(env, job)
    assert job.status is JobStatus.RUNNING


async def test_mark_job_failed_leaves_a_finished_job_alone(env: Env) -> None:
    job = await _queued_job(env, CSV_BOM)
    await _run(env, job)

    await mark_job_failed(
        job.id, "STORAGE_UNAVAILABLE", "Storage down.", session_factory=env.factory
    )

    job, bom = await _reload(env, job)
    assert job.status is JobStatus.SUCCEEDED
    assert bom.status is UploadStatus.PARSED
