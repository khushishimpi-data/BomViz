"""Database guarantees of `bom_uploads` and `import_jobs`.

The partial unique indexes enforce two business rules at the database level: the same file
(checksum) cannot be uploaded twice into one workspace unless the first copy is soft-deleted,
and a BOM has at most one active (QUEUED or RUNNING) job.
"""

import uuid

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BomUpload,
    FileFormat,
    ImportJob,
    JobStatus,
    JobType,
    UploadStatus,
    User,
    Workspace,
)

pytestmark = pytest.mark.integration

CHECKSUM = "a" * 64


async def _add_user(session: AsyncSession) -> User:
    user = User(email="bom-owner@example.com", password_hash="not-a-real-hash", full_name="Owner")
    session.add(user)
    await session.flush()
    return user


async def _add_workspace(session: AsyncSession, owner: User, slug: str) -> Workspace:
    workspace = Workspace(name=slug, slug=slug, owner_id=owner.id)
    session.add(workspace)
    await session.flush()
    return workspace


def _upload(workspace: Workspace, checksum: str = CHECKSUM, **overrides: object) -> BomUpload:
    bom_id = uuid.uuid4()
    values: dict[str, object] = {
        "id": bom_id,
        "workspace_id": workspace.id,
        "bom_name": "Main BOM",
        "original_filename": "bom.csv",
        "file_format": FileFormat.CSV,
        "content_type": "text/csv",
        "size_bytes": 123,
        "checksum_sha256": checksum,
        "original_file_key": f"workspaces/{workspace.id}/boms/{bom_id}/original.csv",
    }
    values.update(overrides)
    return BomUpload(**values)


async def _add_upload(session: AsyncSession, upload: BomUpload) -> BomUpload:
    session.add(upload)
    await session.flush()
    return upload


@pytest.fixture
async def workspace(db_session: AsyncSession) -> Workspace:
    owner = await _add_user(db_session)
    return await _add_workspace(db_session, owner, "bom-workspace")


async def test_new_upload_gets_database_defaults(
    db_session: AsyncSession, workspace: Workspace
) -> None:
    upload = await _add_upload(db_session, _upload(workspace))
    await db_session.commit()
    await db_session.refresh(upload)

    assert upload.status is UploadStatus.UPLOADED
    assert upload.is_deleted is False
    assert upload.uploaded_at is not None


async def test_rejects_duplicate_checksum_in_same_workspace(
    db_session: AsyncSession, workspace: Workspace
) -> None:
    await _add_upload(db_session, _upload(workspace))

    with pytest.raises(IntegrityError, match="uq_bom_uploads_workspace_id_checksum_sha256"):
        await _add_upload(db_session, _upload(workspace))


async def test_allows_same_checksum_in_another_workspace(
    db_session: AsyncSession, workspace: Workspace
) -> None:
    other = await _add_workspace(
        db_session, await db_session.get_one(User, workspace.owner_id), "other"
    )
    await _add_upload(db_session, _upload(workspace))

    second = await _add_upload(db_session, _upload(other))

    assert second.workspace_id == other.id


async def test_allows_same_checksum_when_first_upload_is_deleted(
    db_session: AsyncSession, workspace: Workspace
) -> None:
    await _add_upload(db_session, _upload(workspace, is_deleted=True))

    second = await _add_upload(db_session, _upload(workspace))

    assert second.is_deleted is False


async def test_rejects_non_positive_size(db_session: AsyncSession, workspace: Workspace) -> None:
    with pytest.raises(IntegrityError, match="ck_bom_uploads_size_bytes_positive"):
        await _add_upload(db_session, _upload(workspace, size_bytes=0))


def _job(upload: BomUpload, status: JobStatus) -> ImportJob:
    return ImportJob(bom_upload_id=upload.id, job_type=JobType.PARSE, status=status)


async def test_rejects_second_active_job_for_one_upload(
    db_session: AsyncSession, workspace: Workspace
) -> None:
    upload = await _add_upload(db_session, _upload(workspace))
    db_session.add(_job(upload, JobStatus.RUNNING))
    await db_session.flush()

    db_session.add(_job(upload, JobStatus.QUEUED))
    with pytest.raises(IntegrityError, match="uq_import_jobs_bom_upload_id_active"):
        await db_session.flush()


@pytest.mark.parametrize("finished_status", [JobStatus.SUCCEEDED, JobStatus.FAILED])
async def test_allows_active_job_next_to_finished_jobs(
    db_session: AsyncSession, workspace: Workspace, finished_status: JobStatus
) -> None:
    upload = await _add_upload(db_session, _upload(workspace))
    db_session.add_all([_job(upload, finished_status), _job(upload, finished_status)])
    await db_session.flush()

    active = _job(upload, JobStatus.QUEUED)
    db_session.add(active)
    await db_session.flush()

    assert active.id is not None


async def test_rejects_negative_attempts(db_session: AsyncSession, workspace: Workspace) -> None:
    upload = await _add_upload(db_session, _upload(workspace))
    job = _job(upload, JobStatus.QUEUED)
    job.attempts = -1
    db_session.add(job)

    with pytest.raises(IntegrityError, match="ck_import_jobs_attempts_non_negative"):
        await db_session.flush()
