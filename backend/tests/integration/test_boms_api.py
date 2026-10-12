"""BOM upload, list, detail, file download, re-parse and preview over HTTP against PostgreSQL.

Object storage is a `FakeStorage` and the job queue a recording fake (both injected through
dependency overrides). Covers every upload error code, duplicate handling, product/revision
scoping, role permissions (403) and tenant isolation (404), object cleanup when the database
write fails, enqueue failures, signed URLs with their audit rows, the upload rate limit, and list
filters/pagination/search escaping; re-parse options and allowlists, state checks, active and
stale jobs, and the raw preview.
"""

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from openpyxl import Workbook
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.datastructures import UploadFile
from starlette.types import Message, Receive, Scope, Send

from app.api import boms as boms_api
from app.api.boms import get_bom_service
from app.core.config import Settings
from app.core.dependencies import DbSessionDep, SettingsDep, get_rate_limiter, get_storage
from app.main import create_app
from app.models import AuditLog, BomUpload, ImportJob, User
from app.repositories.bom_repository import BomRepository
from app.services import bom_service, parse_job_service
from app.services.bom_service import BomService
from app.services.job_queue import JobQueue, get_job_queue
from app.services.parse_job_service import run_parse_job
from app.services.rate_limiter import RateLimiter
from app.services.upload_intake import FORM_OVERHEAD_BYTES, OLE_SIGNATURE, XLSX_SIGNATURE
from tests.conftest import make_settings
from tests.fakes import FailingJobQueue, FakeRedis, FakeStorage, RecordingJobQueue

pytestmark = pytest.mark.integration

SECRET = "test-secret-key-that-is-long-enough-for-hs256-0123456789"
PASSWORD = "a very long passphrase"
API = "/api/v1"
WORKSPACES = f"{API}/workspaces"
BOMS = f"{API}/boms"
UPLOAD = f"{BOMS}/upload"
MAX_UPLOAD_BYTES = 64 * 1024
SIGNED_URL_TTL = 600
XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@dataclass
class Env:
    client: AsyncClient
    app: FastAPI
    storage: FakeStorage
    queue: RecordingJobQueue


def _settings(database_url: str, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "database_url": database_url,
        "jwt_secret_key": SECRET,
        "rate_limit_enabled": False,
        "max_upload_bytes": MAX_UPLOAD_BYTES,
        "signed_url_ttl_seconds": SIGNED_URL_TTL,
    }
    values.update(overrides)
    return make_settings(**values)


async def _start(
    settings: Settings,
    queue: JobQueue | None = None,
    *,
    override_queue: bool = True,
    redis: FakeRedis | None = None,
) -> AsyncIterator[Env]:
    """Run an app on `FakeStorage`. The job queue is `queue`, else a `RecordingJobQueue`;
    with `override_queue=False` no queue is configured (`app.state.job_queue` is cleared after
    startup). With `redis`, rate limits count in that in-memory Redis (both the dependency and
    `app.state.redis`)."""
    app = create_app(settings)
    storage = FakeStorage()
    recording = RecordingJobQueue()
    app.dependency_overrides[get_storage] = lambda: storage
    if redis is not None:
        app.dependency_overrides[get_rate_limiter] = lambda: RateLimiter(redis, settings)
    if override_queue:
        chosen: JobQueue = queue or recording
        app.dependency_overrides[get_job_queue] = lambda: chosen
    async with app.router.lifespan_context(app):
        if redis is not None:
            # The upload route class counts its body limit before dependencies are resolved,
            # through `app.state.redis` rather than the overridable dependency.
            app.state.redis = redis
        if not override_queue:
            app.state.job_queue = None
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield Env(client=client, app=app, storage=storage, queue=recording)


@pytest.fixture
async def env(migrated_database_url: str, truncate_tables: None) -> AsyncIterator[Env]:
    async for started in _start(_settings(migrated_database_url)):
        yield started


@pytest.fixture
async def failing_queue_env(
    migrated_database_url: str, truncate_tables: None
) -> AsyncIterator[Env]:
    async for started in _start(_settings(migrated_database_url), FailingJobQueue()):
        yield started


@pytest.fixture
async def unconfigured_queue_env(
    migrated_database_url: str, truncate_tables: None
) -> AsyncIterator[Env]:
    async for started in _start(_settings(migrated_database_url), override_queue=False):
        yield started


@pytest.fixture
async def limited_env(migrated_database_url: str, truncate_tables: None) -> AsyncIterator[Env]:
    settings = _settings(migrated_database_url, rate_limit_enabled=True)
    async for started in _start(settings, redis=FakeRedis()):
        yield started


# Helpers


@dataclass(frozen=True)
class Actor:
    id: str
    email: str
    headers: dict[str, str]


async def _signup(client: AsyncClient, email: str) -> Actor:
    registered = await client.post(
        f"{API}/auth/register",
        json={"email": email, "password": PASSWORD, "full_name": "Test User"},
    )
    assert registered.status_code == 201, registered.text
    login = await client.post(f"{API}/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200, login.text
    client.cookies.clear()
    return Actor(
        id=registered.json()["id"],
        email=email,
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    )


async def _workspace(client: AsyncClient, actor: Actor, name: str = "Acme") -> str:
    response = await client.post(WORKSPACES, json={"name": name}, headers=actor.headers)
    assert response.status_code == 201, response.text
    workspace_id: str = response.json()["id"]
    return workspace_id


async def _member(client: AsyncClient, admin: Actor, workspace_id: str, role: str) -> Actor:
    actor = await _signup(client, f"{role.lower()}-{uuid.uuid4().hex[:8]}@example.com")
    response = await client.post(
        f"{WORKSPACES}/{workspace_id}/members",
        json={"email": actor.email, "role": role},
        headers=admin.headers,
    )
    assert response.status_code == 201, response.text
    return actor


@dataclass(frozen=True)
class Setup:
    admin: Actor
    workspace_id: str


async def _setup(client: AsyncClient) -> Setup:
    admin = await _signup(client, "admin@example.com")
    return Setup(admin=admin, workspace_id=await _workspace(client, admin))


async def _product(client: AsyncClient, actor: Actor, workspace_id: str, code: str) -> str:
    response = await client.post(
        f"{WORKSPACES}/{workspace_id}/products",
        json={"product_code": code, "name": f"Product {code}"},
        headers=actor.headers,
    )
    assert response.status_code == 201, response.text
    product_id: str = response.json()["id"]
    return product_id


async def _revision(client: AsyncClient, actor: Actor, product_id: str) -> str:
    response = await client.post(
        f"{API}/products/{product_id}/revisions",
        json={"revision_code": "A"},
        headers=actor.headers,
    )
    assert response.status_code == 201, response.text
    revision_id: str = response.json()["id"]
    return revision_id


def _csv(marker: str | None = None) -> bytes:
    """A small CSV whose bytes are unique unless `marker` is repeated."""
    return f"Part Number,Quantity\nP-{marker or uuid.uuid4().hex},2\n".encode()


def _xlsx(tmp_path: Path) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append(["Part Number", "Quantity"])
    sheet.append(["P-1", 2])
    path = tmp_path / "bom.xlsx"
    workbook.save(path)
    return path.read_bytes()


def _form(workspace_id: str, **fields: str) -> dict[str, str]:
    return {"workspace_id": workspace_id, "bom_name": "Main BOM", **fields}


async def _upload(
    client: AsyncClient,
    actor: Actor,
    workspace_id: str,
    content: bytes | None = None,
    filename: str = "bom.csv",
    **fields: str,
) -> Response:
    payload = _csv() if content is None else content
    return await client.post(
        UPLOAD,
        data=_form(workspace_id, **fields),
        files={"file": (filename, payload, "application/octet-stream")},
        headers=actor.headers,
    )


async def _uploaded(
    client: AsyncClient, actor: Actor, workspace_id: str, **kwargs: Any
) -> dict[str, Any]:
    response = await _upload(client, actor, workspace_id, **kwargs)
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def _error(response: Response) -> tuple[int, str]:
    return response.status_code, response.json()["error"]["code"]


async def _audits(session: AsyncSession, action: str) -> list[AuditLog]:
    session.expire_all()
    result = await session.scalars(
        select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.created_at)
    )
    return list(result.all())


async def _bom_rows(session: AsyncSession) -> list[BomUpload]:
    session.expire_all()
    return list((await session.scalars(select(BomUpload))).all())


async def _list(
    client: AsyncClient, actor: Actor, workspace_id: str, **params: Any
) -> dict[str, Any]:
    response = await client.get(
        BOMS, params={"workspace_id": workspace_id, **params}, headers=actor.headers
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _names(page: dict[str, Any]) -> list[str]:
    return [item["bom_name"] for item in page["items"]]


class BodyCountingApp:
    """ASGI wrapper counting the request body bytes the application actually pulled."""

    def __init__(self, app: FastAPI) -> None:
        self._app = app
        self.received = 0

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async def counting_receive() -> Message:
            message = await receive()
            if message["type"] == "http.request":
                self.received += len(message.get("body", b""))
            return message

        await self._app(scope, counting_receive, send)


@asynccontextmanager
async def _counting_client(app: FastAPI) -> AsyncIterator[tuple[AsyncClient, BodyCountingApp]]:
    counter = BodyCountingApp(app)
    transport = ASGITransport(app=counter)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client, counter


async def _streamed_upload(
    client: AsyncClient,
    workspace_id: str,
    content: bytes,
    headers: dict[str, str] | None = None,
    filename: str = "bom.csv",
) -> Response:
    """Send an upload as a chunked stream (no Content-Length), 16 KiB per body message."""
    prepared = client.build_request(
        "POST", UPLOAD, data=_form(workspace_id), files={"file": (filename, content, "text/csv")}
    )
    body = prepared.read()

    async def chunks() -> AsyncIterator[bytes]:
        for start in range(0, len(body), 16 * 1024):
            yield body[start : start + 16 * 1024]

    return await client.post(
        UPLOAD,
        content=chunks(),
        headers={**(headers or {}), "Content-Type": prepared.headers["Content-Type"]},
    )


# Upload: success


async def test_an_engineer_uploads_a_csv(env: Env, db_session: AsyncSession) -> None:
    setup = await _setup(env.client)
    engineer = await _member(env.client, setup.admin, setup.workspace_id, "ENGINEER")
    content = _csv()

    response = await _upload(
        env.client,
        engineer,
        setup.workspace_id,
        content=content,
        bom_name="  Main BOM  ",
        revision_label=" A ",
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["bom_name"] == "Main BOM"
    assert body["revision_label"] == "A"
    assert body["workspace_id"] == setup.workspace_id
    assert body["original_filename"] == "bom.csv"
    assert body["file_format"] == "CSV"
    assert body["size_bytes"] == len(content)
    assert body["status"] == "UPLOADED"
    assert body["uploaded_by_id"] == engineer.id
    assert body["row_count"] is None
    assert body["parse_options"] is None
    assert body["product_id"] is None
    assert len(body["jobs"]) == 1
    assert body["latest_job"] == body["jobs"][0]
    assert body["latest_job"]["job_type"] == "PARSE"
    assert body["latest_job"]["status"] == "QUEUED"
    assert body["latest_job"]["attempts"] == 0
    assert "celery_task_id" not in body["latest_job"]


async def test_upload_stores_the_original_under_an_id_based_key(
    env: Env, db_session: AsyncSession
) -> None:
    setup = await _setup(env.client)
    content = _csv()

    body = await _uploaded(env.client, setup.admin, setup.workspace_id, content=content)

    key = f"workspaces/{setup.workspace_id}/boms/{body['id']}/original.csv"
    assert list(env.storage.objects) == [key]
    assert env.storage.objects[key].data == content
    assert env.storage.objects[key].content_type == "text/csv"


async def test_object_key_never_contains_the_client_filename(env: Env) -> None:
    setup = await _setup(env.client)

    body = await _uploaded(
        env.client, setup.admin, setup.workspace_id, filename="../../secret plan.csv"
    )

    assert body["original_filename"] == "secret plan.csv"
    [key] = env.storage.objects
    assert "secret" not in key
    assert ".." not in key


async def test_upload_enqueues_the_parse_job_after_commit(
    env: Env, db_session: AsyncSession
) -> None:
    setup = await _setup(env.client)

    body = await _uploaded(env.client, setup.admin, setup.workspace_id)

    job_id = uuid.UUID(body["latest_job"]["id"])
    assert env.queue.parse_jobs == [job_id]
    job = await db_session.get(ImportJob, job_id)
    assert job is not None
    assert job.celery_task_id == env.queue.task_ids[job_id]
    assert uuid.UUID(job.celery_task_id)
    assert job.parameters == {}
    assert str(job.requested_by_id) == setup.admin.id


async def test_upload_writes_an_audit_row_without_file_details(
    env: Env, db_session: AsyncSession
) -> None:
    setup = await _setup(env.client)
    content = _csv()

    body = await _uploaded(env.client, setup.admin, setup.workspace_id, content=content)

    [audit] = await _audits(db_session, "BOM_UPLOADED")
    assert str(audit.entity_id) == body["id"]
    assert str(audit.actor_id) == setup.admin.id
    assert str(audit.workspace_id) == setup.workspace_id
    assert audit.action_metadata == {"file_format": "CSV", "size_bytes": len(content)}


async def test_upload_detects_xlsx_from_contents(env: Env, tmp_path: Path) -> None:
    setup = await _setup(env.client)

    body = await _uploaded(
        env.client, setup.admin, setup.workspace_id, content=_xlsx(tmp_path), filename="b.XLSX"
    )

    assert body["file_format"] == "XLSX"
    [stored] = env.storage.objects.values()
    assert stored.content_type == XLSX_CONTENT_TYPE


async def test_upload_detects_xls_from_contents(env: Env) -> None:
    setup = await _setup(env.client)

    body = await _uploaded(
        env.client,
        setup.admin,
        setup.workspace_id,
        content=OLE_SIGNATURE + b"\x00" * 512,
        filename="legacy.xls",
    )

    assert body["file_format"] == "XLS"
    [key] = env.storage.objects
    assert key.endswith("/original.xls")


# Upload: file errors


async def test_a_declared_content_length_over_the_limit_is_rejected_before_reading(
    env: Env,
) -> None:
    setup = await _setup(env.client)
    request = env.client.build_request(
        "POST",
        UPLOAD,
        data=_form(setup.workspace_id),
        files={"file": ("bom.csv", _csv(), "text/csv")},
        headers=setup.admin.headers,
    )
    request.headers["Content-Length"] = str(MAX_UPLOAD_BYTES + FORM_OVERHEAD_BYTES + 1)

    response = await env.client.send(request)

    assert _error(response) == (413, "FILE_TOO_LARGE")
    assert env.storage.objects == {}


async def test_a_file_over_the_limit_is_rejected_while_streaming(
    env: Env, db_session: AsyncSession
) -> None:
    setup = await _setup(env.client)

    response = await _upload(
        env.client, setup.admin, setup.workspace_id, content=b"a" * (MAX_UPLOAD_BYTES + 1)
    )

    assert _error(response) == (413, "FILE_TOO_LARGE")
    assert env.storage.objects == {}
    assert await _bom_rows(db_session) == []


async def test_a_file_at_the_limit_is_accepted(env: Env) -> None:
    setup = await _setup(env.client)

    response = await _upload(
        env.client, setup.admin, setup.workspace_id, content=b"a" * MAX_UPLOAD_BYTES
    )

    assert response.status_code == 201, response.text


async def test_an_oversized_body_without_content_length_is_cut_off(env: Env) -> None:
    setup = await _setup(env.client)
    prepared = env.client.build_request(
        "POST",
        UPLOAD,
        data=_form(setup.workspace_id),
        files={"file": ("bom.csv", b"a" * (MAX_UPLOAD_BYTES + FORM_OVERHEAD_BYTES), "text/csv")},
    )
    body = prepared.read()

    async def chunks() -> AsyncIterator[bytes]:
        for start in range(0, len(body), 16 * 1024):
            yield body[start : start + 16 * 1024]

    response = await env.client.post(
        UPLOAD,
        content=chunks(),
        headers={**setup.admin.headers, "Content-Type": prepared.headers["Content-Type"]},
    )

    assert _error(response) == (413, "FILE_TOO_LARGE")
    assert env.storage.objects == {}


async def test_an_empty_file_is_rejected(env: Env) -> None:
    setup = await _setup(env.client)

    response = await _upload(env.client, setup.admin, setup.workspace_id, content=b"")

    assert _error(response) == (400, "EMPTY_FILE")


@pytest.mark.parametrize("filename", ["bom.txt", "bom.pdf", "bom"])
async def test_an_unsupported_extension_is_rejected(env: Env, filename: str) -> None:
    setup = await _setup(env.client)

    response = await _upload(env.client, setup.admin, setup.workspace_id, filename=filename)

    assert _error(response) == (400, "UNSUPPORTED_FILE_TYPE")


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("bom.xlsx", b"Part Number,Quantity\n"),
        ("bom.xls", XLSX_SIGNATURE + b"\x00" * 64),
        ("bom.csv", XLSX_SIGNATURE + b"\x00" * 64),
    ],
    ids=["text-as-xlsx", "zip-as-xls", "zip-as-csv"],
)
async def test_contents_that_do_not_match_the_extension_are_rejected(
    env: Env, filename: str, content: bytes
) -> None:
    setup = await _setup(env.client)

    response = await _upload(
        env.client, setup.admin, setup.workspace_id, content=content, filename=filename
    )

    assert _error(response) == (400, "UNSUPPORTED_FILE_TYPE")


async def test_a_csv_containing_a_nul_byte_is_rejected(env: Env) -> None:
    setup = await _setup(env.client)

    response = await _upload(
        env.client, setup.admin, setup.workspace_id, content=b"Part,Qty\nP-1\x00,2\n"
    )

    assert _error(response) == (400, "UNSUPPORTED_FILE_TYPE")


async def test_an_ole_file_named_xlsx_is_reported_as_encrypted(env: Env) -> None:
    setup = await _setup(env.client)

    response = await _upload(
        env.client,
        setup.admin,
        setup.workspace_id,
        content=OLE_SIGNATURE + b"\x00" * 512,
        filename="locked.xlsx",
    )

    assert _error(response) == (400, "ENCRYPTED_WORKBOOK")


# Upload: form validation


async def test_a_blank_bom_name_is_rejected(env: Env) -> None:
    setup = await _setup(env.client)

    response = await _upload(env.client, setup.admin, setup.workspace_id, bom_name="   ")

    assert _error(response) == (422, "VALIDATION_ERROR")


async def test_a_bom_name_over_200_characters_is_rejected(env: Env) -> None:
    setup = await _setup(env.client)

    response = await _upload(env.client, setup.admin, setup.workspace_id, bom_name="n" * 201)

    assert _error(response) == (422, "VALIDATION_ERROR")


async def test_a_revision_label_over_64_characters_is_rejected(env: Env) -> None:
    setup = await _setup(env.client)

    response = await _upload(env.client, setup.admin, setup.workspace_id, revision_label="r" * 65)

    assert _error(response) == (422, "VALIDATION_ERROR")


async def test_a_missing_file_is_rejected(env: Env) -> None:
    setup = await _setup(env.client)

    response = await env.client.post(
        UPLOAD, data=_form(setup.workspace_id), headers=setup.admin.headers
    )

    assert _error(response) == (422, "VALIDATION_ERROR")


# Upload: duplicates


async def test_the_same_file_twice_in_a_workspace_is_a_duplicate(
    env: Env, db_session: AsyncSession
) -> None:
    setup = await _setup(env.client)
    content = _csv("same")
    first = await _uploaded(env.client, setup.admin, setup.workspace_id, content=content)

    response = await _upload(
        env.client, setup.admin, setup.workspace_id, content=content, filename="renamed.csv"
    )

    assert _error(response) == (409, "DUPLICATE_UPLOAD")
    assert response.json()["error"]["details"] == [{"existing_bom_id": first["id"]}]
    assert len(env.storage.objects) == 1
    assert len(await _bom_rows(db_session)) == 1


async def test_the_same_file_in_another_workspace_is_allowed(env: Env) -> None:
    setup = await _setup(env.client)
    other_workspace = await _workspace(env.client, setup.admin, "Other")
    content = _csv("same")
    await _uploaded(env.client, setup.admin, setup.workspace_id, content=content)

    response = await _upload(env.client, setup.admin, other_workspace, content=content)

    assert response.status_code == 201, response.text


async def test_a_deleted_upload_does_not_block_the_same_file(
    env: Env, db_session: AsyncSession
) -> None:
    setup = await _setup(env.client)
    content = _csv("same")
    first = await _uploaded(env.client, setup.admin, setup.workspace_id, content=content)
    await db_session.execute(
        update(BomUpload).where(BomUpload.id == uuid.UUID(first["id"])).values(is_deleted=True)
    )
    await db_session.commit()

    response = await _upload(env.client, setup.admin, setup.workspace_id, content=content)

    assert response.status_code == 201, response.text


async def test_a_concurrent_duplicate_hitting_the_unique_index_is_a_duplicate(
    env: Env, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup = await _setup(env.client)
    content = _csv("race")
    first = await _uploaded(env.client, setup.admin, setup.workspace_id, content=content)
    original = BomRepository.find_duplicate
    calls = 0

    async def misses_the_first_lookup(
        self: BomRepository, workspace_id: uuid.UUID, checksum: str
    ) -> uuid.UUID | None:
        # The pre-insert check runs before the other upload commits, so it finds nothing.
        nonlocal calls
        calls += 1
        return None if calls == 1 else await original(self, workspace_id, checksum)

    monkeypatch.setattr(BomRepository, "find_duplicate", misses_the_first_lookup)

    response = await _upload(env.client, setup.admin, setup.workspace_id, content=content)

    assert _error(response) == (409, "DUPLICATE_UPLOAD")
    assert response.json()["error"]["details"] == [{"existing_bom_id": first["id"]}]
    assert len(env.storage.objects) == 1
    assert len(await _bom_rows(db_session)) == 1


# Upload: infrastructure failures


async def test_a_failed_database_write_deletes_the_stored_object(
    env: Env, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup = await _setup(env.client)

    async def broken_insert(self: BomRepository, bom: BomUpload, job: ImportJob) -> None:
        raise OperationalError("INSERT INTO bom_uploads", {}, Exception("connection reset"))

    monkeypatch.setattr(BomRepository, "add_upload", broken_insert)

    response = await _upload(env.client, setup.admin, setup.workspace_id)

    assert _error(response) == (500, "INTERNAL_ERROR")
    assert env.storage.objects == {}
    assert await _bom_rows(db_session) == []
    assert env.queue.parse_jobs == []


async def test_unavailable_storage_is_a_503(env: Env, db_session: AsyncSession) -> None:
    setup = await _setup(env.client)
    env.storage.failing_operations.add("put_file")

    response = await _upload(env.client, setup.admin, setup.workspace_id)

    assert _error(response) == (503, "STORAGE_UNAVAILABLE")
    assert await _bom_rows(db_session) == []
    assert await _audits(db_session, "BOM_UPLOADED") == []


def _override_service(env: Env, on_session: Callable[[AsyncSession], None]) -> None:
    """Build the upload's `BomService` as the app does, handing its request session to
    `on_session` first."""

    def service(session: DbSessionDep, settings: SettingsDep) -> BomService:
        on_session(session)
        return BomService(session, env.storage, settings)

    env.app.dependency_overrides[get_bom_service] = service


async def test_no_database_transaction_is_open_while_the_file_is_copied_or_stored(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup = await _setup(env.client)
    sessions: list[AsyncSession] = []
    _override_service(env, sessions.append)
    in_transaction: dict[str, list[bool]] = {"copy": [], "store": []}
    original_read = UploadFile.read
    original_put = env.storage.put_file

    async def probing_read(self: UploadFile, size: int = -1) -> bytes:
        in_transaction["copy"].append(sessions[0].in_transaction())
        return await original_read(self, size)

    async def probing_put(key: str, path: Path, content_type: str) -> None:
        in_transaction["store"].append(sessions[0].in_transaction())
        await original_put(key, path, content_type)

    monkeypatch.setattr(UploadFile, "read", probing_read)
    monkeypatch.setattr(env.storage, "put_file", probing_put)

    response = await _upload(env.client, setup.admin, setup.workspace_id)

    assert response.status_code == 201, response.text
    assert in_transaction["copy"]
    assert not any(in_transaction["copy"])
    assert in_transaction["store"] == [False]


def _fail_the_insert_commit(
    env: Env, monkeypatch: pytest.MonkeyPatch, failure: Callable[[], Awaitable[None]]
) -> None:
    """Make the upload's commit of its new rows run `failure` instead of committing."""
    inserted = False
    original_add = BomRepository.add_upload

    async def add_upload(self: BomRepository, bom: BomUpload, job: ImportJob) -> None:
        nonlocal inserted
        await original_add(self, bom, job)
        inserted = True

    def patch_commit(session: AsyncSession) -> None:
        original_commit = session.commit

        async def commit() -> None:
            if inserted:
                await failure()
            else:
                await original_commit()

        monkeypatch.setattr(session, "commit", commit)

    monkeypatch.setattr(BomRepository, "add_upload", add_upload)
    _override_service(env, patch_commit)


def _stored_bom_id(env: Env) -> str:
    [key] = env.storage.objects
    return key.split("/boms/", 1)[1].split("/", 1)[0]


@pytest.mark.parametrize(
    "error",
    [
        OperationalError("COMMIT", {}, Exception("server closed the connection")),
        InterfaceError("COMMIT", {}, Exception("connection is closed")),
    ],
    ids=["operational", "interface"],
)
async def test_a_commit_with_an_unknown_outcome_keeps_the_object_and_logs_it(
    env: Env,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    error: Exception,
) -> None:
    setup = await _setup(env.client)

    async def connection_lost() -> None:
        raise error

    _fail_the_insert_commit(env, monkeypatch, connection_lost)

    with caplog.at_level(logging.ERROR, logger=bom_service.__name__):
        response = await _upload(
            env.client, setup.admin, setup.workspace_id, filename="secret-plan.csv"
        )

    assert _error(response) == (500, "INTERNAL_ERROR")
    bom_id = _stored_bom_id(env)
    [record] = [r for r in caplog.records if r.getMessage() == "bom_upload_commit_outcome_unknown"]
    assert record.levelno == logging.ERROR
    assert record.__dict__["bom_upload_id"] == bom_id
    assert "secret-plan" not in str(record.__dict__)
    assert await _bom_rows(db_session) == []


async def test_a_definite_commit_failure_deletes_the_object(
    env: Env, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup = await _setup(env.client)

    async def constraint_violated() -> None:
        raise IntegrityError("COMMIT", {}, Exception("violates foreign key constraint"))

    _fail_the_insert_commit(env, monkeypatch, constraint_violated)

    response = await _upload(env.client, setup.admin, setup.workspace_id)

    assert _error(response) == (500, "INTERNAL_ERROR")
    assert env.storage.objects == {}
    assert await _bom_rows(db_session) == []


async def test_a_request_cancelled_before_the_commit_deletes_the_stored_object(
    env: Env, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup = await _setup(env.client)
    reached_insert = asyncio.Event()

    async def hangs(self: BomRepository, bom: BomUpload, job: ImportJob) -> None:
        reached_insert.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(BomRepository, "add_upload", hangs)
    upload = asyncio.create_task(_upload(env.client, setup.admin, setup.workspace_id))
    await reached_insert.wait()
    assert len(env.storage.objects) == 1

    upload.cancel()
    with pytest.raises(asyncio.CancelledError):
        await upload

    assert env.storage.objects == {}
    assert await _bom_rows(db_session) == []


async def test_a_request_cancelled_during_the_commit_keeps_the_object_and_logs_it(
    env: Env, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    setup = await _setup(env.client)
    committing = asyncio.Event()

    async def hangs() -> None:
        committing.set()
        await asyncio.Event().wait()

    _fail_the_insert_commit(env, monkeypatch, hangs)
    with caplog.at_level(logging.ERROR, logger=bom_service.__name__):
        upload = asyncio.create_task(_upload(env.client, setup.admin, setup.workspace_id))
        await committing.wait()
        upload.cancel()
        with pytest.raises(asyncio.CancelledError):
            await upload

    bom_id = _stored_bom_id(env)
    [record] = [r for r in caplog.records if r.getMessage() == "bom_upload_commit_outcome_unknown"]
    assert record.__dict__["bom_upload_id"] == bom_id


async def test_an_enqueue_failure_marks_the_job_and_upload_failed(
    failing_queue_env: Env, db_session: AsyncSession
) -> None:
    env = failing_queue_env
    setup = await _setup(env.client)

    response = await _upload(env.client, setup.admin, setup.workspace_id)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "PARSE_FAILED"
    assert body["latest_job"]["status"] == "FAILED"
    assert body["latest_job"]["error_code"] == "QUEUE_UNAVAILABLE"
    assert body["latest_job"]["finished_at"] is not None
    [row] = await _bom_rows(db_session)
    assert row.status == "PARSE_FAILED"
    job = await db_session.get(ImportJob, uuid.UUID(body["latest_job"]["id"]))
    assert job is not None
    assert job.status == "FAILED"
    assert job.error_code == "QUEUE_UNAVAILABLE"
    assert len(env.storage.objects) == 1


async def test_without_a_configured_queue_the_job_is_marked_queue_unavailable(
    unconfigured_queue_env: Env,
) -> None:
    env = unconfigured_queue_env
    setup = await _setup(env.client)

    body = await _uploaded(env.client, setup.admin, setup.workspace_id)

    assert body["status"] == "PARSE_FAILED"
    assert body["latest_job"]["error_code"] == "QUEUE_UNAVAILABLE"


# Upload: product scoping


async def test_upload_links_a_product_and_revision_of_the_workspace(env: Env) -> None:
    setup = await _setup(env.client)
    product_id = await _product(env.client, setup.admin, setup.workspace_id, "PRD-1")
    revision_id = await _revision(env.client, setup.admin, product_id)

    body = await _uploaded(
        env.client,
        setup.admin,
        setup.workspace_id,
        product_id=product_id,
        product_revision_id=revision_id,
    )

    assert body["product_id"] == product_id
    assert body["product_revision_id"] == revision_id


async def test_a_product_from_another_workspace_is_not_found(env: Env) -> None:
    setup = await _setup(env.client)
    other_workspace = await _workspace(env.client, setup.admin, "Other")
    foreign_product = await _product(env.client, setup.admin, other_workspace, "PRD-X")

    response = await _upload(
        env.client, setup.admin, setup.workspace_id, product_id=foreign_product
    )

    assert _error(response) == (404, "NOT_FOUND")
    assert env.storage.objects == {}


async def test_a_revision_of_another_product_is_not_found(env: Env) -> None:
    setup = await _setup(env.client)
    product_id = await _product(env.client, setup.admin, setup.workspace_id, "PRD-1")
    other_product = await _product(env.client, setup.admin, setup.workspace_id, "PRD-2")
    other_revision = await _revision(env.client, setup.admin, other_product)

    response = await _upload(
        env.client,
        setup.admin,
        setup.workspace_id,
        product_id=product_id,
        product_revision_id=other_revision,
    )

    assert _error(response) == (404, "NOT_FOUND")


async def test_a_revision_without_its_product_is_rejected(env: Env) -> None:
    setup = await _setup(env.client)
    product_id = await _product(env.client, setup.admin, setup.workspace_id, "PRD-1")
    revision_id = await _revision(env.client, setup.admin, product_id)

    response = await _upload(
        env.client, setup.admin, setup.workspace_id, product_revision_id=revision_id
    )

    assert _error(response) == (422, "VALIDATION_ERROR")


# Upload: authentication, permissions, tenancy, rate limit


async def test_upload_requires_authentication(env: Env) -> None:
    setup = await _setup(env.client)

    response = await env.client.post(
        UPLOAD, data=_form(setup.workspace_id), files={"file": ("bom.csv", _csv(), "text/csv")}
    )

    assert _error(response) == (401, "UNAUTHENTICATED")


LARGE_BODY = b"a" * (5 * 1024 * 1024)
UNREAD_BODY_BYTES = 64 * 1024  # Less than this was pulled: the body was never parsed.


async def test_an_unauthenticated_upload_is_rejected_before_its_body_is_read(env: Env) -> None:
    setup = await _setup(env.client)

    async with _counting_client(env.app) as (client, counter):
        response = await _streamed_upload(client, setup.workspace_id, LARGE_BODY)

    assert _error(response) == (401, "UNAUTHENTICATED")
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert counter.received < UNREAD_BODY_BYTES


@pytest.mark.parametrize(
    "authorization", ["Bearer not-a-jwt", "Basic dXNlcjpwYXNz"], ids=["bad-token", "basic"]
)
async def test_an_upload_with_an_unusable_authorization_is_rejected_before_its_body_is_read(
    env: Env, authorization: str
) -> None:
    setup = await _setup(env.client)

    async with _counting_client(env.app) as (client, counter):
        response = await _streamed_upload(
            client, setup.workspace_id, LARGE_BODY, headers={"Authorization": authorization}
        )

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert counter.received < UNREAD_BODY_BYTES


async def test_an_upload_by_a_deactivated_user_is_rejected_before_its_body_is_read(
    env: Env, db_session: AsyncSession
) -> None:
    setup = await _setup(env.client)
    await db_session.execute(
        update(User).where(User.id == uuid.UUID(setup.admin.id)).values(is_active=False)
    )
    await db_session.commit()

    async with _counting_client(env.app) as (client, counter):
        response = await _streamed_upload(
            client, setup.workspace_id, LARGE_BODY, headers=setup.admin.headers
        )

    assert _error(response) == (401, "TOKEN_INVALID")
    assert counter.received < UNREAD_BODY_BYTES


async def test_the_upload_body_limit_rejects_before_the_body_is_read(
    limited_env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = limited_env
    monkeypatch.setattr(boms_api, "BOM_UPLOAD_BODY_RATE_LIMIT", (2, 3600))
    setup = await _setup(env.client)
    for _ in range(2):
        response = await _upload(env.client, setup.admin, setup.workspace_id, filename="x.txt")
        assert _error(response) == (400, "UNSUPPORTED_FILE_TYPE")

    async with _counting_client(env.app) as (client, counter):
        response = await _streamed_upload(
            client, setup.workspace_id, LARGE_BODY, headers=setup.admin.headers
        )

    assert _error(response) == (429, "RATE_LIMITED")
    assert int(response.headers["Retry-After"]) > 0
    assert counter.received < UNREAD_BODY_BYTES


async def test_the_upload_body_limit_is_counted_per_user(
    limited_env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = limited_env
    monkeypatch.setattr(boms_api, "BOM_UPLOAD_BODY_RATE_LIMIT", (1, 3600))
    setup = await _setup(env.client)
    engineer = await _member(env.client, setup.admin, setup.workspace_id, "ENGINEER")
    await _uploaded(env.client, setup.admin, setup.workspace_id)
    limited = await _upload(env.client, setup.admin, setup.workspace_id)

    response = await _upload(env.client, engineer, setup.workspace_id)

    assert _error(limited) == (429, "RATE_LIMITED")
    assert response.status_code == 201, response.text


@pytest.mark.parametrize("length", [chr(0xB2), "1e9"], ids=["superscript-two", "exponent"])
async def test_a_content_length_that_is_not_ascii_digits_is_a_bad_request(
    env: Env, length: str
) -> None:
    setup = await _setup(env.client)
    request = env.client.build_request(
        "POST",
        UPLOAD,
        data=_form(setup.workspace_id),
        files={"file": ("bom.csv", _csv(), "text/csv")},
        headers=setup.admin.headers,
    )
    # Latin-1, as servers decode header bytes: "²" arrives as the single byte 0xB2.
    request.headers.encoding = "latin-1"
    request.headers["Content-Length"] = length

    response = await env.client.send(request)

    assert _error(response) == (400, "BAD_REQUEST")
    assert env.storage.objects == {}


@pytest.mark.parametrize("role", ["VIEWER", "PROCUREMENT", "PRODUCTION"])
async def test_read_only_roles_cannot_upload(env: Env, role: str) -> None:
    setup = await _setup(env.client)
    reader = await _member(env.client, setup.admin, setup.workspace_id, role)

    response = await _upload(env.client, reader, setup.workspace_id)

    assert _error(response) == (403, "FORBIDDEN")
    assert env.storage.objects == {}


async def test_a_non_member_cannot_upload(env: Env) -> None:
    setup = await _setup(env.client)
    outsider = await _signup(env.client, "outsider@example.com")

    response = await _upload(env.client, outsider, setup.workspace_id)

    assert _error(response) == (404, "NOT_FOUND")


async def test_upload_to_a_nonexistent_workspace_is_not_found(env: Env) -> None:
    setup = await _setup(env.client)

    response = await _upload(env.client, setup.admin, str(uuid.uuid4()))

    assert _error(response) == (404, "NOT_FOUND")


async def test_the_31st_upload_in_an_hour_is_rate_limited(limited_env: Env) -> None:
    env = limited_env
    setup = await _setup(env.client)
    for _ in range(30):
        response = await _upload(env.client, setup.admin, setup.workspace_id, filename="x.txt")
        assert _error(response) == (400, "UNSUPPORTED_FILE_TYPE")

    response = await _upload(env.client, setup.admin, setup.workspace_id)

    assert _error(response) == (429, "RATE_LIMITED")
    assert int(response.headers["Retry-After"]) > 0


# Detail


async def test_detail_returns_the_bom_with_its_jobs(env: Env) -> None:
    setup = await _setup(env.client)
    uploaded = await _uploaded(env.client, setup.admin, setup.workspace_id)

    response = await env.client.get(f"{BOMS}/{uploaded['id']}", headers=setup.admin.headers)

    assert response.status_code == 200, response.text
    assert response.json() == uploaded


async def test_detail_lists_the_last_ten_jobs_newest_first(
    env: Env, db_session: AsyncSession
) -> None:
    setup = await _setup(env.client)
    uploaded = await _uploaded(env.client, setup.admin, setup.workspace_id)
    bom_id = uuid.UUID(uploaded["id"])
    await db_session.execute(update(ImportJob).values(status="SUCCEEDED"))
    for _ in range(11):
        db_session.add(ImportJob(bom_upload_id=bom_id, job_type="PARSE", status="FAILED"))
        await db_session.commit()

    response = await env.client.get(f"{BOMS}/{bom_id}", headers=setup.admin.headers)

    body = response.json()
    assert len(body["jobs"]) == 10
    queued = [job["queued_at"] for job in body["jobs"]]
    assert queued == sorted(queued, reverse=True)
    assert uploaded["latest_job"]["id"] not in {job["id"] for job in body["jobs"]}
    assert body["latest_job"] == body["jobs"][0]


async def test_a_viewer_reads_the_detail(env: Env) -> None:
    setup = await _setup(env.client)
    viewer = await _member(env.client, setup.admin, setup.workspace_id, "VIEWER")
    uploaded = await _uploaded(env.client, setup.admin, setup.workspace_id)

    response = await env.client.get(f"{BOMS}/{uploaded['id']}", headers=viewer.headers)

    assert response.status_code == 200, response.text


async def test_a_non_member_cannot_see_the_detail(env: Env) -> None:
    setup = await _setup(env.client)
    outsider = await _signup(env.client, "outsider@example.com")
    uploaded = await _uploaded(env.client, setup.admin, setup.workspace_id)

    response = await env.client.get(f"{BOMS}/{uploaded['id']}", headers=outsider.headers)

    assert _error(response) == (404, "NOT_FOUND")
    assert response.json()["error"]["message"] == "BOM not found."


async def test_an_unknown_bom_is_not_found(env: Env) -> None:
    setup = await _setup(env.client)

    response = await env.client.get(f"{BOMS}/{uuid.uuid4()}", headers=setup.admin.headers)

    assert _error(response) == (404, "NOT_FOUND")


async def test_a_deleted_bom_is_not_found(env: Env, db_session: AsyncSession) -> None:
    setup = await _setup(env.client)
    uploaded = await _uploaded(env.client, setup.admin, setup.workspace_id)
    await db_session.execute(update(BomUpload).values(is_deleted=True))
    await db_session.commit()

    response = await env.client.get(f"{BOMS}/{uploaded['id']}", headers=setup.admin.headers)

    assert _error(response) == (404, "NOT_FOUND")


async def test_detail_requires_authentication(env: Env) -> None:
    response = await env.client.get(f"{BOMS}/{uuid.uuid4()}")

    assert _error(response) == (401, "UNAUTHENTICATED")


# File download


async def test_file_returns_a_signed_url_for_the_original(env: Env) -> None:
    setup = await _setup(env.client)
    uploaded = await _uploaded(
        env.client, setup.admin, setup.workspace_id, filename="Stückliste final.csv"
    )

    response = await env.client.get(f"{BOMS}/{uploaded['id']}/file", headers=setup.admin.headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"url", "expires_at"}
    [presign] = env.storage.presign_requests
    assert presign.key == f"workspaces/{setup.workspace_id}/boms/{uploaded['id']}/original.csv"
    assert presign.filename == "Stückliste final.csv"
    assert presign.content_type == "text/csv"
    assert presign.ttl_seconds == SIGNED_URL_TTL
    assert "filename*=UTF-8''St%C3%BCckliste%20final.csv" in presign.content_disposition
    assert body["url"].startswith("https://storage.test/")


async def test_each_file_request_is_audited_without_the_url(
    env: Env, db_session: AsyncSession
) -> None:
    setup = await _setup(env.client)
    viewer = await _member(env.client, setup.admin, setup.workspace_id, "VIEWER")
    uploaded = await _uploaded(env.client, setup.admin, setup.workspace_id)

    response = await env.client.get(f"{BOMS}/{uploaded['id']}/file", headers=viewer.headers)

    assert response.status_code == 200, response.text
    [audit] = await _audits(db_session, "BOM_FILE_DOWNLOADED")
    assert str(audit.entity_id) == uploaded["id"]
    assert str(audit.actor_id) == viewer.id
    assert str(audit.workspace_id) == setup.workspace_id
    assert audit.action_metadata == {}


async def test_file_with_storage_down_is_a_503_and_not_audited(
    env: Env, db_session: AsyncSession
) -> None:
    setup = await _setup(env.client)
    uploaded = await _uploaded(env.client, setup.admin, setup.workspace_id)
    env.storage.failing_operations.add("presigned_download_url")

    response = await env.client.get(f"{BOMS}/{uploaded['id']}/file", headers=setup.admin.headers)

    assert _error(response) == (503, "STORAGE_UNAVAILABLE")
    assert await _audits(db_session, "BOM_FILE_DOWNLOADED") == []


async def test_a_non_member_cannot_download_the_file(env: Env, db_session: AsyncSession) -> None:
    setup = await _setup(env.client)
    outsider = await _signup(env.client, "outsider@example.com")
    uploaded = await _uploaded(env.client, setup.admin, setup.workspace_id)

    response = await env.client.get(f"{BOMS}/{uploaded['id']}/file", headers=outsider.headers)

    assert _error(response) == (404, "NOT_FOUND")
    assert env.storage.presign_requests == []
    assert await _audits(db_session, "BOM_FILE_DOWNLOADED") == []


# List


async def test_list_returns_the_workspace_boms_newest_first(env: Env) -> None:
    setup = await _setup(env.client)
    other_workspace = await _workspace(env.client, setup.admin, "Other")
    for name in ("First", "Second", "Third"):
        await _uploaded(env.client, setup.admin, setup.workspace_id, bom_name=name)
    await _uploaded(env.client, setup.admin, other_workspace, bom_name="Elsewhere")

    page = await _list(env.client, setup.admin, setup.workspace_id)

    assert _names(page) == ["Third", "Second", "First"]
    assert page["total"] == 3
    assert page["items"][0]["latest_job"]["status"] == "QUEUED"
    assert "jobs" not in page["items"][0]


async def test_list_paginates(env: Env) -> None:
    setup = await _setup(env.client)
    for name in ("First", "Second", "Third"):
        await _uploaded(env.client, setup.admin, setup.workspace_id, bom_name=name)

    page = await _list(env.client, setup.admin, setup.workspace_id, limit=2, offset=1)

    assert _names(page) == ["Second", "First"]
    assert (page["total"], page["limit"], page["offset"]) == (3, 2, 1)


async def test_list_filters_by_status(failing_queue_env: Env, db_session: AsyncSession) -> None:
    env = failing_queue_env
    setup = await _setup(env.client)
    await _uploaded(env.client, setup.admin, setup.workspace_id, bom_name="Failed")
    await db_session.execute(update(BomUpload).values(status="PARSED"))
    await db_session.commit()
    await _uploaded(env.client, setup.admin, setup.workspace_id, bom_name="Failed too")

    page = await _list(env.client, setup.admin, setup.workspace_id, status="PARSE_FAILED")

    assert _names(page) == ["Failed too"]


async def test_list_filters_by_product(env: Env) -> None:
    setup = await _setup(env.client)
    product_id = await _product(env.client, setup.admin, setup.workspace_id, "PRD-1")
    await _uploaded(
        env.client, setup.admin, setup.workspace_id, bom_name="Linked", product_id=product_id
    )
    await _uploaded(env.client, setup.admin, setup.workspace_id, bom_name="Unlinked")

    page = await _list(env.client, setup.admin, setup.workspace_id, product_id=product_id)

    assert _names(page) == ["Linked"]


async def test_list_searches_name_filename_and_revision_label(env: Env) -> None:
    setup = await _setup(env.client)
    await _uploaded(env.client, setup.admin, setup.workspace_id, bom_name="Gearbox main")
    await _uploaded(
        env.client, setup.admin, setup.workspace_id, bom_name="One", filename="GEARBOX-v2.csv"
    )
    await _uploaded(
        env.client, setup.admin, setup.workspace_id, bom_name="Two", revision_label="gearbox-b"
    )
    await _uploaded(env.client, setup.admin, setup.workspace_id, bom_name="Unrelated")

    page = await _list(env.client, setup.admin, setup.workspace_id, q=" gearbox ")

    assert sorted(_names(page)) == ["Gearbox main", "One", "Two"]


@pytest.mark.parametrize(("query", "expected"), [("100%", ["100% cotton"]), ("a_b", ["a_b"])])
async def test_list_search_treats_wildcards_literally(
    env: Env, query: str, expected: list[str]
) -> None:
    setup = await _setup(env.client)
    for name in ("100% cotton", "1000 cotton", "a_b", "axb"):
        await _uploaded(env.client, setup.admin, setup.workspace_id, bom_name=name)

    page = await _list(env.client, setup.admin, setup.workspace_id, q=query)

    assert _names(page) == expected


async def test_list_hides_deleted_boms(env: Env, db_session: AsyncSession) -> None:
    setup = await _setup(env.client)
    await _uploaded(env.client, setup.admin, setup.workspace_id, bom_name="Gone")
    await db_session.execute(update(BomUpload).values(is_deleted=True))
    await db_session.commit()
    await _uploaded(env.client, setup.admin, setup.workspace_id, bom_name="Kept")

    page = await _list(env.client, setup.admin, setup.workspace_id)

    assert _names(page) == ["Kept"]
    assert page["total"] == 1


async def test_a_viewer_lists_boms(env: Env) -> None:
    setup = await _setup(env.client)
    viewer = await _member(env.client, setup.admin, setup.workspace_id, "VIEWER")
    await _uploaded(env.client, setup.admin, setup.workspace_id, bom_name="Visible")

    page = await _list(env.client, viewer, setup.workspace_id)

    assert _names(page) == ["Visible"]


async def test_a_non_member_cannot_list(env: Env) -> None:
    setup = await _setup(env.client)
    outsider = await _signup(env.client, "outsider@example.com")

    response = await env.client.get(
        BOMS, params={"workspace_id": setup.workspace_id}, headers=outsider.headers
    )

    assert _error(response) == (404, "NOT_FOUND")


async def test_list_requires_a_workspace_id(env: Env) -> None:
    setup = await _setup(env.client)

    response = await env.client.get(BOMS, headers=setup.admin.headers)

    assert _error(response) == (422, "VALIDATION_ERROR")


async def test_list_requires_authentication(env: Env) -> None:
    response = await env.client.get(BOMS, params={"workspace_id": str(uuid.uuid4())})

    assert _error(response) == (401, "UNAUTHENTICATED")


# Re-parse and preview


STALE_AGE = timedelta(minutes=16)


def _parse_url(bom_id: str) -> str:
    return f"{BOMS}/{bom_id}/parse"


def _preview_url(bom_id: str) -> str:
    return f"{BOMS}/{bom_id}/preview"


async def _run_latest_job(env: Env, bom: dict[str, Any]) -> None:
    """Run the BOM's latest parse job the way the worker would (as the task it was sent as)."""
    job_id = uuid.UUID(bom["latest_job"]["id"])
    async with env.app.state.sessionmaker() as session:
        task_id = await session.scalar(
            select(ImportJob.celery_task_id).where(ImportJob.id == job_id)
        )
    assert task_id is not None
    await run_parse_job(
        job_id,
        task_id=task_id,
        session_factory=env.app.state.sessionmaker,
        storage=env.storage,
        settings=env.app.state.settings,
    )


async def _parsed(env: Env, actor: Actor, workspace_id: str) -> dict[str, Any]:
    bom = await _uploaded(env.client, actor, workspace_id)
    await _run_latest_job(env, bom)
    return bom


async def _job(session: AsyncSession, job_id: str) -> ImportJob:
    session.expire_all()
    job = await session.get(ImportJob, uuid.UUID(job_id), populate_existing=True)
    assert job is not None
    return job


async def _set_job(session: AsyncSession, job_id: str, **values: Any) -> None:
    await session.execute(
        update(ImportJob).where(ImportJob.id == uuid.UUID(job_id)).values(**values)
    )
    await session.commit()


async def _set_bom_status(session: AsyncSession, bom_id: str, status: str) -> None:
    await session.execute(
        update(BomUpload).where(BomUpload.id == uuid.UUID(bom_id)).values(status=status)
    )
    await session.commit()


class VisibilityCheckingQueue:
    """Records, for each enqueued job, whether a separate connection could already see it with
    the task id it is being sent as."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self.visible: dict[uuid.UUID, bool] = {}

    def enqueue_parse(self, job_id: uuid.UUID, task_id: str) -> None:
        # Called in a worker thread, so it can run its own event loop.
        self.visible[job_id] = asyncio.run(self._committed(job_id, task_id))

    async def _committed(self, job_id: uuid.UUID, task_id: str) -> bool:
        engine = create_async_engine(self._database_url, poolclass=NullPool)
        try:
            async with engine.connect() as connection:
                stored = await connection.scalar(
                    select(ImportJob.celery_task_id).where(ImportJob.id == job_id)
                )
            return stored == task_id
        finally:
            await engine.dispose()


class WorkerWinsTheRaceQueue:
    """The broker accepts the task and a worker starts the job, but the publish still reports
    an error to the API (e.g. the connection drops before the acknowledgement)."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def enqueue_parse(self, job_id: uuid.UUID, task_id: str) -> None:
        asyncio.run(self._start(job_id))
        raise ConnectionError("Connection reset by peer.")

    async def _start(self, job_id: uuid.UUID) -> None:
        engine = create_async_engine(self._database_url, poolclass=NullPool)
        try:
            async with engine.begin() as connection:
                bom_id = await connection.scalar(
                    update(ImportJob)
                    .where(ImportJob.id == job_id)
                    .values(status="RUNNING", attempts=1, started_at=datetime.now(UTC))
                    .returning(ImportJob.bom_upload_id)
                )
                await connection.execute(
                    update(BomUpload).where(BomUpload.id == bom_id).values(status="PARSING")
                )
        finally:
            await engine.dispose()


async def test_a_reparse_queues_a_job_with_the_requested_options(
    env: Env, db_session: AsyncSession
) -> None:
    setup = await _setup(env.client)
    engineer = await _member(env.client, setup.admin, setup.workspace_id, "ENGINEER")
    bom = await _parsed(env, setup.admin, setup.workspace_id)

    response = await env.client.post(
        _parse_url(bom["id"]),
        json={"delimiter": ";", "header_row": 2, "encoding": "cp1252"},
        headers=engineer.headers,
    )

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["job_type"] == "PARSE"
    assert body["status"] == "QUEUED"
    assert body["attempts"] == 0
    assert body["error_code"] is None
    job = await _job(db_session, body["id"])
    assert job.parameters == {"delimiter": ";", "header_row": 2, "encoding": "cp1252"}
    assert str(job.requested_by_id) == engineer.id
    assert env.queue.parse_jobs[-1] == job.id
    assert job.celery_task_id == env.queue.task_ids[job.id]


async def test_a_reparse_stores_only_the_provided_options(
    env: Env, db_session: AsyncSession
) -> None:
    setup = await _setup(env.client)
    bom = await _parsed(env, setup.admin, setup.workspace_id)

    response = await env.client.post(
        _parse_url(bom["id"]), json={"sheet_name": None}, headers=setup.admin.headers
    )

    assert response.status_code == 202, response.text
    job = await _job(db_session, response.json()["id"])
    assert job.parameters == {}


async def test_a_reparse_without_a_body_detects_everything(
    env: Env, db_session: AsyncSession
) -> None:
    setup = await _setup(env.client)
    bom = await _parsed(env, setup.admin, setup.workspace_id)

    response = await env.client.post(_parse_url(bom["id"]), headers=setup.admin.headers)

    assert response.status_code == 202, response.text
    job = await _job(db_session, response.json()["id"])
    assert job.parameters == {}


async def test_a_reparse_is_enqueued_after_its_job_is_committed(
    migrated_database_url: str, truncate_tables: None
) -> None:
    queue = VisibilityCheckingQueue(migrated_database_url)
    async for env in _start(_settings(migrated_database_url), queue):
        setup = await _setup(env.client)
        bom = await _uploaded(env.client, setup.admin, setup.workspace_id)
        await _run_latest_job(env, bom)

        response = await env.client.post(_parse_url(bom["id"]), headers=setup.admin.headers)

        assert response.status_code == 202, response.text
        assert queue.visible[uuid.UUID(response.json()["id"])] is True


async def test_a_reparse_is_audited_with_the_job_id(env: Env, db_session: AsyncSession) -> None:
    setup = await _setup(env.client)
    bom = await _parsed(env, setup.admin, setup.workspace_id)

    response = await env.client.post(_parse_url(bom["id"]), headers=setup.admin.headers)

    [audit] = await _audits(db_session, "BOM_PARSE_REQUESTED")
    assert str(audit.entity_id) == bom["id"]
    assert str(audit.actor_id) == setup.admin.id
    assert str(audit.workspace_id) == setup.workspace_id
    assert audit.action_metadata == {"job_id": response.json()["id"]}


async def test_a_failed_parse_can_be_reparsed(env: Env, db_session: AsyncSession) -> None:
    setup = await _setup(env.client)
    bom = await _uploaded(env.client, setup.admin, setup.workspace_id)
    await _set_job(db_session, bom["latest_job"]["id"], status="FAILED")
    await _set_bom_status(db_session, bom["id"], "PARSE_FAILED")

    response = await env.client.post(_parse_url(bom["id"]), headers=setup.admin.headers)

    assert response.status_code == 202, response.text


async def test_a_queued_job_blocks_a_reparse(env: Env) -> None:
    setup = await _setup(env.client)
    bom = await _uploaded(env.client, setup.admin, setup.workspace_id)

    response = await env.client.post(_parse_url(bom["id"]), headers=setup.admin.headers)

    assert _error(response) == (409, "JOB_IN_PROGRESS")


async def test_a_running_job_started_recently_blocks_a_reparse(
    env: Env, db_session: AsyncSession
) -> None:
    setup = await _setup(env.client)
    bom = await _uploaded(env.client, setup.admin, setup.workspace_id)
    now = datetime.now(UTC)
    await _set_job(
        db_session,
        bom["latest_job"]["id"],
        status="RUNNING",
        queued_at=now - STALE_AGE,
        started_at=now,
    )
    await _set_bom_status(db_session, bom["id"], "PARSING")

    response = await env.client.post(_parse_url(bom["id"]), headers=setup.admin.headers)

    assert _error(response) == (409, "JOB_IN_PROGRESS")


async def test_a_stale_queued_job_is_failed_and_replaced(
    env: Env, db_session: AsyncSession
) -> None:
    setup = await _setup(env.client)
    bom = await _uploaded(env.client, setup.admin, setup.workspace_id)
    stale_id = bom["latest_job"]["id"]
    await _set_job(db_session, stale_id, queued_at=datetime.now(UTC) - STALE_AGE)

    response = await env.client.post(_parse_url(bom["id"]), headers=setup.admin.headers)

    assert response.status_code == 202, response.text
    assert response.json()["id"] != stale_id
    stale = await _job(db_session, stale_id)
    assert stale.status == "FAILED"
    assert stale.error_code == "STALE_JOB"
    assert stale.finished_at is not None


async def test_a_stale_running_job_is_failed_and_replaced(
    env: Env, db_session: AsyncSession
) -> None:
    setup = await _setup(env.client)
    bom = await _uploaded(env.client, setup.admin, setup.workspace_id)
    stale_id = bom["latest_job"]["id"]
    await _set_job(db_session, stale_id, status="RUNNING", started_at=datetime.now(UTC) - STALE_AGE)
    await _set_bom_status(db_session, bom["id"], "PARSING")

    response = await env.client.post(_parse_url(bom["id"]), headers=setup.admin.headers)

    assert response.status_code == 202, response.text
    stale = await _job(db_session, stale_id)
    assert stale.error_code == "STALE_JOB"
    [row] = await _bom_rows(db_session)
    assert row.status == "PARSE_FAILED"


async def test_a_concurrent_reparse_hitting_the_active_job_index_is_in_progress(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup = await _setup(env.client)
    bom = await _uploaded(env.client, setup.admin, setup.workspace_id)

    async def miss_the_active_job(*_: object) -> None:
        """As if the competing job was committed after this request looked for one."""

    monkeypatch.setattr(
        parse_job_service.ParseRequestService, "_retire_stale_job", miss_the_active_job
    )

    response = await env.client.post(_parse_url(bom["id"]), headers=setup.admin.headers)

    assert _error(response) == (409, "JOB_IN_PROGRESS")


@pytest.mark.parametrize("status", ["PARSING", "MAPPED", "IMPORTED"])
async def test_a_bom_in_another_state_cannot_be_reparsed(
    env: Env, db_session: AsyncSession, status: str
) -> None:
    setup = await _setup(env.client)
    bom = await _uploaded(env.client, setup.admin, setup.workspace_id)
    await _set_job(db_session, bom["latest_job"]["id"], status="SUCCEEDED")
    await _set_bom_status(db_session, bom["id"], status)

    response = await env.client.post(_parse_url(bom["id"]), headers=setup.admin.headers)

    assert _error(response) == (409, "INVALID_STATE")


@pytest.mark.parametrize(
    "payload",
    [
        {"delimiter": ":"},
        {"delimiter": ",,"},
        {"encoding": "ascii"},
        {"header_row": 0},
        {"header_row": 1001},
        {"sheet_name": ""},
        {"sheet_name": "x" * 256},
        {"skip_rows": 1},
    ],
)
async def test_parse_options_outside_the_allowlists_are_rejected(
    env: Env, payload: dict[str, Any]
) -> None:
    setup = await _setup(env.client)
    bom = await _parsed(env, setup.admin, setup.workspace_id)

    response = await env.client.post(
        _parse_url(bom["id"]), json=payload, headers=setup.admin.headers
    )

    assert _error(response) == (422, "VALIDATION_ERROR")


async def test_an_enqueue_failure_returns_the_failed_reparse_job(
    failing_queue_env: Env, db_session: AsyncSession
) -> None:
    env = failing_queue_env
    setup = await _setup(env.client)
    bom = await _uploaded(env.client, setup.admin, setup.workspace_id)

    response = await env.client.post(_parse_url(bom["id"]), headers=setup.admin.headers)

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "FAILED"
    assert body["error_code"] == "QUEUE_UNAVAILABLE"
    assert body["finished_at"] is not None
    [row] = await _bom_rows(db_session)
    assert row.status == "PARSE_FAILED"


async def test_an_enqueue_error_after_the_worker_started_the_upload_job_leaves_it_running(
    migrated_database_url: str, truncate_tables: None, db_session: AsyncSession
) -> None:
    async for env in _start(
        _settings(migrated_database_url), WorkerWinsTheRaceQueue(migrated_database_url)
    ):
        setup = await _setup(env.client)

        body = await _uploaded(env.client, setup.admin, setup.workspace_id)

        assert body["status"] == "PARSING"
        assert body["latest_job"]["status"] == "RUNNING"
        assert body["latest_job"]["error_code"] is None
        job = await _job(db_session, body["latest_job"]["id"])
        assert job.status == "RUNNING"
        assert job.finished_at is None


async def test_an_enqueue_error_after_the_worker_started_the_reparse_job_leaves_it_running(
    env: Env, migrated_database_url: str, db_session: AsyncSession
) -> None:
    setup = await _setup(env.client)
    bom = await _parsed(env, setup.admin, setup.workspace_id)
    racing: JobQueue = WorkerWinsTheRaceQueue(migrated_database_url)
    env.app.dependency_overrides[get_job_queue] = lambda: racing

    response = await env.client.post(_parse_url(bom["id"]), headers=setup.admin.headers)

    assert response.status_code == 202, response.text
    assert response.json()["status"] == "RUNNING"
    [row] = await _bom_rows(db_session)
    assert row.status == "PARSING"


@pytest.mark.parametrize("role", ["VIEWER", "PROCUREMENT", "PRODUCTION"])
async def test_read_only_roles_cannot_reparse(env: Env, role: str) -> None:
    setup = await _setup(env.client)
    member = await _member(env.client, setup.admin, setup.workspace_id, role)
    bom = await _parsed(env, setup.admin, setup.workspace_id)

    response = await env.client.post(_parse_url(bom["id"]), headers=member.headers)

    assert _error(response) == (403, "FORBIDDEN")


async def test_a_non_member_cannot_reparse(env: Env) -> None:
    setup = await _setup(env.client)
    outsider = await _signup(env.client, "outsider@example.com")
    bom = await _parsed(env, setup.admin, setup.workspace_id)

    response = await env.client.post(_parse_url(bom["id"]), headers=outsider.headers)

    assert _error(response) == (404, "NOT_FOUND")


async def test_reparse_requires_authentication(env: Env) -> None:
    response = await env.client.post(_parse_url(str(uuid.uuid4())))

    assert _error(response) == (401, "UNAUTHENTICATED")


async def test_the_preview_returns_the_parse_snapshot_and_target_fields(env: Env) -> None:
    setup = await _setup(env.client)
    viewer = await _member(env.client, setup.admin, setup.workspace_id, "VIEWER")
    bom = await _uploaded(
        env.client,
        setup.admin,
        setup.workspace_id,
        content=b"Part Number,Description,Qty\nP-1,Bolt,4\n",
    )
    await _run_latest_job(env, bom)

    response = await env.client.get(_preview_url(bom["id"]), headers=viewer.headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "PARSED"
    assert body["parse_options"] == {
        "sheet_name": None,
        "header_row": 1,
        "delimiter": ",",
        "encoding": "utf-8",
    }
    assert body["sheets"] == []
    assert body["columns"] == [
        {"index": 0, "name": "Part Number"},
        {"index": 1, "name": "Description"},
        {"index": 2, "name": "Qty"},
    ]
    assert body["row_count"] == 1
    assert body["column_count"] == 3
    assert body["skipped_empty_rows"] == 0
    assert body["formula_cells_without_value"] == 0
    assert body["rows"] == [{"row_number": 2, "cells": ["P-1", "Bolt", "4"]}]
    assert [item["target_field"] for item in body["suggested_mapping"]] == [
        "part_number",
        "description",
        "quantity",
    ]
    assert body["missing_required"] == ["part_name"]
    assert body["target_fields"][0] == {
        "key": "part_number",
        "label": "Part Number",
        "required": True,
    }
    assert {"key": "notes", "label": "Notes", "required": False} in body["target_fields"]


async def test_the_preview_reports_formula_cells_without_saved_values(
    env: Env, tmp_path: Path
) -> None:
    setup = await _setup(env.client)
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append(["Part Number", "Part Name", "Qty", "Unit Cost", "Total Cost"])
    sheet.append(["P-1", "Bolt", 4, 2, "=C2*D2"])
    path = tmp_path / "formulas.xlsx"
    workbook.save(path)
    bom = await _uploaded(
        env.client,
        setup.admin,
        setup.workspace_id,
        content=path.read_bytes(),
        filename="formulas.xlsx",
    )
    await _run_latest_job(env, bom)

    response = await env.client.get(_preview_url(bom["id"]), headers=setup.admin.headers)

    assert response.status_code == 200, response.text
    assert response.json()["formula_cells_without_value"] == 1
    assert response.json()["rows"][0]["cells"][4] == ""


async def test_a_snapshot_without_the_formula_count_previews_it_as_zero(
    env: Env, db_session: AsyncSession
) -> None:
    setup = await _setup(env.client)
    bom = await _uploaded(env.client, setup.admin, setup.workspace_id)
    await _run_latest_job(env, bom)
    row = await db_session.get(BomUpload, uuid.UUID(bom["id"]))
    assert row is not None and row.parse_result is not None
    old_snapshot = {k: v for k, v in row.parse_result.items() if k != "formula_cells_without_value"}
    await db_session.execute(
        update(BomUpload)
        .where(BomUpload.id == uuid.UUID(bom["id"]))
        .values(parse_result=old_snapshot)
    )
    await db_session.commit()

    response = await env.client.get(_preview_url(bom["id"]), headers=setup.admin.headers)

    assert response.status_code == 200, response.text
    assert response.json()["formula_cells_without_value"] == 0


async def test_the_preview_accepts_an_explicit_raw_stage(env: Env) -> None:
    setup = await _setup(env.client)
    bom = await _parsed(env, setup.admin, setup.workspace_id)

    response = await env.client.get(
        _preview_url(bom["id"]), params={"stage": "raw"}, headers=setup.admin.headers
    )

    assert response.status_code == 200, response.text


async def test_the_preview_of_an_unparsed_bom_is_not_available(env: Env) -> None:
    setup = await _setup(env.client)
    bom = await _uploaded(env.client, setup.admin, setup.workspace_id)

    response = await env.client.get(_preview_url(bom["id"]), headers=setup.admin.headers)

    assert _error(response) == (409, "NOT_PARSED")


async def test_the_preview_after_a_failed_parse_is_not_available(
    env: Env, db_session: AsyncSession
) -> None:
    setup = await _setup(env.client)
    bom = await _parsed(env, setup.admin, setup.workspace_id)
    await _set_bom_status(db_session, bom["id"], "PARSE_FAILED")

    response = await env.client.get(_preview_url(bom["id"]), headers=setup.admin.headers)

    assert _error(response) == (409, "NOT_PARSED")


async def test_the_normalized_preview_stage_is_rejected(env: Env) -> None:
    setup = await _setup(env.client)
    bom = await _parsed(env, setup.admin, setup.workspace_id)

    response = await env.client.get(
        _preview_url(bom["id"]), params={"stage": "normalized"}, headers=setup.admin.headers
    )

    assert _error(response) == (422, "VALIDATION_ERROR")
    [detail] = response.json()["error"]["details"]
    assert detail["field"] == "query.stage"
    assert "stage=raw" in detail["message"]


async def test_an_unknown_preview_stage_is_rejected(env: Env) -> None:
    setup = await _setup(env.client)
    bom = await _parsed(env, setup.admin, setup.workspace_id)

    response = await env.client.get(
        _preview_url(bom["id"]), params={"stage": "cooked"}, headers=setup.admin.headers
    )

    assert _error(response) == (422, "VALIDATION_ERROR")


async def test_a_non_member_cannot_see_the_preview(env: Env) -> None:
    setup = await _setup(env.client)
    outsider = await _signup(env.client, "outsider@example.com")
    bom = await _parsed(env, setup.admin, setup.workspace_id)

    response = await env.client.get(_preview_url(bom["id"]), headers=outsider.headers)

    assert _error(response) == (404, "NOT_FOUND")


async def test_preview_requires_authentication(env: Env) -> None:
    response = await env.client.get(_preview_url(str(uuid.uuid4())))

    assert _error(response) == (401, "UNAUTHENTICATED")
