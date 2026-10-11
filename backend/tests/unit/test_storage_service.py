import io
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit

import boto3
import pytest
from botocore.exceptions import (
    ConnectTimeoutError,
    EndpointConnectionError,
    NoCredentialsError,
    ReadTimeoutError,
)
from botocore.response import StreamingBody
from botocore.stub import ANY, Stubber

from app.core.config import Settings
from app.services.storage_service import (
    MAX_DOWNLOAD_FILENAME_CHARS,
    ObjectStorage,
    StorageObjectNotFoundError,
    StorageUnavailableError,
    content_disposition,
)
from app.services.upload_intake import clean_filename
from tests.conftest import make_settings
from tests.fakes import FakeStorage

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
GENERIC_MESSAGE = "File storage is temporarily unavailable. Please try again later."


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "s3_endpoint_url": "http://minio:9000",
        "s3_public_endpoint_url": "http://localhost:9000",
        "s3_access_key_id": "test-access-key",
        "s3_secret_access_key": "test-secret-key",
        "s3_bucket": "bomviz",
    }
    values.update(overrides)
    return make_settings(**values)


def _stubbed_client() -> Any:
    # Never reaches the network: the Stubber answers every call.
    return boto3.client(
        "s3",
        endpoint_url="http://storage.invalid:9000",
        aws_access_key_id="test-access-key",
        aws_secret_access_key="test-secret-key",
        region_name="us-east-1",
    )


@pytest.fixture
def stubbed() -> Iterator[tuple[ObjectStorage, Stubber]]:
    client = _stubbed_client()
    with Stubber(client) as stubber:
        yield ObjectStorage(_settings(), client=client, clock=lambda: NOW), stubber
        stubber.assert_no_pending_responses()


class _RaisingClient:
    """A boto3-like client whose every call fails with the given exception."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    def _raise(self, *args: Any, **kwargs: Any) -> Any:
        raise self._error

    put_object = get_object = delete_object = generate_presigned_url = _raise


class _BrokenBody:
    """A response body that yields one chunk, then times out mid-stream."""

    def iter_chunks(self, chunk_size: int) -> Iterator[bytes]:
        yield b"partial"
        raise ReadTimeoutError(endpoint_url="http://minio:9000")

    def close(self) -> None:
        pass


class _BrokenStreamClient:
    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        return {"Body": _BrokenBody()}


# --- content_disposition ---------------------------------------------------------------------


def test_disposition_for_an_ascii_name_uses_the_name_in_both_parameters() -> None:
    assert content_disposition("bom.csv") == (
        "attachment; filename=\"bom.csv\"; filename*=UTF-8''bom.csv"
    )


def test_disposition_replaces_non_ascii_in_the_fallback_and_percent_encodes_utf8() -> None:
    assert content_disposition("Stückliste ü.xlsx") == (
        "attachment; filename=\"St_ckliste _.xlsx\"; filename*=UTF-8''St%C3%BCckliste%20%C3%BC.xlsx"
    )


def test_disposition_removes_quotes_and_backslashes_from_the_fallback() -> None:
    header = content_disposition('my "best" bom.csv')

    assert header == (
        "attachment; filename=\"my best bom.csv\"; filename*=UTF-8''my%20%22best%22%20bom.csv"
    )


@pytest.mark.parametrize(
    "filename",
    ["../../etc/bom.csv", "C:\\Users\\eng\\bom.csv", "/abs/path/bom.csv", "dir\\..\\bom.csv"],
)
def test_disposition_keeps_only_the_last_path_component(filename: str) -> None:
    assert content_disposition(filename) == (
        "attachment; filename=\"bom.csv\"; filename*=UTF-8''bom.csv"
    )


def test_disposition_strips_control_characters_so_headers_cannot_be_injected() -> None:
    header = content_disposition("bom\r\nX-Injected: 1\t.csv\x00")

    assert header == (
        "attachment; filename=\"bomX-Injected: 1.csv\"; filename*=UTF-8''bomX-Injected%3A%201.csv"
    )


def test_disposition_strips_line_and_paragraph_separators() -> None:
    header = content_disposition(f"bom{chr(0x2028)}final{chr(0x2029)}.csv")

    assert header == "attachment; filename=\"bomfinal.csv\"; filename*=UTF-8''bomfinal.csv"


def test_disposition_uses_the_shared_upload_filename_sanitizer() -> None:
    raw = f"C:\\docs\\Stu{chr(0x308)}ck{chr(0x202E)}liste{chr(0x2028)}.csv"

    expected_name = clean_filename(raw, MAX_DOWNLOAD_FILENAME_CHARS)
    assert expected_name == "St\u00fcckliste.csv"
    assert content_disposition(raw).endswith(f"filename*=UTF-8''{quote(expected_name)}")


def test_disposition_truncates_very_long_names_and_keeps_the_extension() -> None:
    header = content_disposition("a" * 300 + ".xlsx")

    fallback = header.split('filename="', 1)[1].split('"', 1)[0]
    assert len(fallback) == MAX_DOWNLOAD_FILENAME_CHARS
    assert fallback == "a" * (MAX_DOWNLOAD_FILENAME_CHARS - 5) + ".xlsx"


def test_disposition_truncates_a_long_name_without_an_extension() -> None:
    header = content_disposition("b" * 400)

    assert f'filename="{"b" * MAX_DOWNLOAD_FILENAME_CHARS}";' in header


@pytest.mark.parametrize("filename", ["", "../", '""', "\r\n"])
def test_disposition_falls_back_to_a_generic_name_when_nothing_usable_remains(
    filename: str,
) -> None:
    assert content_disposition(filename).startswith('attachment; filename="download"')


# --- presigned_download_url ------------------------------------------------------------------


async def test_presigned_url_is_signed_for_the_public_endpoint_host() -> None:
    storage = ObjectStorage(_settings(), clock=lambda: NOW)

    url, _ = await storage.presigned_download_url("ws/1/bom.csv", "bom.csv", "text/csv", 300)

    parts = urlsplit(url)
    assert (parts.scheme, parts.netloc, parts.path) == (
        "http",
        "localhost:9000",
        "/bomviz/ws/1/bom.csv",
    )
    assert "X-Amz-Signature" in parse_qs(parts.query)


async def test_presigned_url_falls_back_to_the_internal_endpoint() -> None:
    storage = ObjectStorage(_settings(s3_public_endpoint_url=None), clock=lambda: NOW)

    url, _ = await storage.presigned_download_url("k", "bom.csv", "text/csv", 300)

    assert urlsplit(url).netloc == "minio:9000"


async def test_presigned_url_carries_disposition_content_type_and_ttl() -> None:
    storage = ObjectStorage(_settings(), clock=lambda: NOW)

    url, _ = await storage.presigned_download_url(
        "k", "Stückliste.xlsx", "application/vnd.ms-excel", 120
    )

    query = parse_qs(urlsplit(url).query)
    assert query["response-content-disposition"] == [content_disposition("Stückliste.xlsx")]
    assert query["response-content-type"] == ["application/vnd.ms-excel"]
    assert query["X-Amz-Expires"] == ["120"]


async def test_presigned_url_expiry_is_now_plus_ttl_in_utc() -> None:
    storage = ObjectStorage(_settings(), clock=lambda: NOW)

    _, expires_at = await storage.presigned_download_url("k", "bom.csv", "text/csv", 300)

    assert expires_at == NOW + timedelta(seconds=300)
    assert expires_at.utcoffset() == timedelta(0)


@pytest.mark.parametrize("ttl", [0, -1, 7 * 24 * 60 * 60 + 1])
async def test_presigned_url_rejects_a_ttl_outside_the_sigv4_range(ttl: int) -> None:
    storage = ObjectStorage(_settings(), clock=lambda: NOW)

    with pytest.raises(ValueError, match="ttl_seconds"):
        await storage.presigned_download_url("k", "bom.csv", "text/csv", ttl)


async def test_presign_failure_maps_to_storage_unavailable() -> None:
    storage = ObjectStorage(_settings(), presign_client=_RaisingClient(NoCredentialsError()))

    with pytest.raises(StorageUnavailableError):
        await storage.presigned_download_url("k", "bom.csv", "text/csv", 300)


# --- put_file / download_to / delete ---------------------------------------------------------


async def test_put_file_uploads_the_file_with_its_content_type(
    stubbed: tuple[ObjectStorage, Stubber], tmp_path: Path
) -> None:
    storage, stubber = stubbed
    source = tmp_path / "bom.csv"
    source.write_bytes(b"a,b\n1,2\n")
    stubber.add_response(
        "put_object",
        {},
        {"Bucket": "bomviz", "Key": "ws/1/bom.csv", "Body": ANY, "ContentType": "text/csv"},
    )

    await storage.put_file("ws/1/bom.csv", source, "text/csv")


async def test_download_to_writes_the_object_bytes(
    stubbed: tuple[ObjectStorage, Stubber], tmp_path: Path
) -> None:
    storage, stubber = stubbed
    data = b"part,qty\nA-1,2\n"
    stubber.add_response(
        "get_object",
        {"Body": StreamingBody(io.BytesIO(data), len(data))},
        {"Bucket": "bomviz", "Key": "ws/1/bom.csv"},
    )
    target = tmp_path / "out.csv"

    await storage.download_to("ws/1/bom.csv", target)

    assert target.read_bytes() == data


async def test_delete_removes_the_object(stubbed: tuple[ObjectStorage, Stubber]) -> None:
    storage, stubber = stubbed
    stubber.add_response("delete_object", {}, {"Bucket": "bomviz", "Key": "ws/1/bom.csv"})

    await storage.delete("ws/1/bom.csv")


# --- error mapping ---------------------------------------------------------------------------


@pytest.mark.parametrize(("code", "status"), [("NoSuchKey", 404), ("404", 404)])
async def test_download_of_a_missing_object_raises_not_found(
    stubbed: tuple[ObjectStorage, Stubber], tmp_path: Path, code: str, status: int
) -> None:
    storage, stubber = stubbed
    stubber.add_client_error("get_object", service_error_code=code, http_status_code=status)
    target = tmp_path / "out.csv"

    with pytest.raises(StorageObjectNotFoundError):
        await storage.download_to("ws/1/missing.csv", target)
    assert not target.exists()


def test_object_not_found_is_not_a_user_facing_error() -> None:
    assert not isinstance(StorageObjectNotFoundError("k"), StorageUnavailableError)


async def test_download_client_error_maps_to_storage_unavailable(
    stubbed: tuple[ObjectStorage, Stubber], tmp_path: Path
) -> None:
    storage, stubber = stubbed
    stubber.add_client_error("get_object", service_error_code="AccessDenied", http_status_code=403)

    with pytest.raises(StorageUnavailableError) as excinfo:
        await storage.download_to("k", tmp_path / "out.csv")

    error = excinfo.value
    assert (error.status_code, error.code, error.message) == (
        503,
        "STORAGE_UNAVAILABLE",
        GENERIC_MESSAGE,
    )


async def test_put_client_error_maps_to_storage_unavailable(
    stubbed: tuple[ObjectStorage, Stubber], tmp_path: Path
) -> None:
    storage, stubber = stubbed
    stubber.add_client_error("put_object", service_error_code="NoSuchBucket", http_status_code=404)
    source = tmp_path / "bom.csv"
    source.write_bytes(b"x")

    with pytest.raises(StorageUnavailableError):
        await storage.put_file("k", source, "text/csv")


async def test_delete_client_error_maps_to_storage_unavailable(
    stubbed: tuple[ObjectStorage, Stubber],
) -> None:
    storage, stubber = stubbed
    stubber.add_client_error(
        "delete_object", service_error_code="InternalError", http_status_code=500
    )

    with pytest.raises(StorageUnavailableError):
        await storage.delete("k")


CONNECTION_ERRORS = [
    EndpointConnectionError(endpoint_url="http://minio:9000"),
    ConnectTimeoutError(endpoint_url="http://minio:9000"),
    ReadTimeoutError(endpoint_url="http://minio:9000"),
]


@pytest.mark.parametrize("error", CONNECTION_ERRORS, ids=lambda e: type(e).__name__)
async def test_connection_failures_map_to_storage_unavailable_for_every_operation(
    error: Exception, tmp_path: Path
) -> None:
    storage = ObjectStorage(_settings(), client=_RaisingClient(error))
    source = tmp_path / "bom.csv"
    source.write_bytes(b"x")

    with pytest.raises(StorageUnavailableError) as put_error:
        await storage.put_file("k", source, "text/csv")
    with pytest.raises(StorageUnavailableError):
        await storage.download_to("k", tmp_path / "out.csv")
    with pytest.raises(StorageUnavailableError):
        await storage.delete("k")

    assert put_error.value.__cause__ is error
    assert "minio" not in put_error.value.message


async def test_download_failing_mid_stream_leaves_no_partial_file(tmp_path: Path) -> None:
    storage = ObjectStorage(_settings(), client=_BrokenStreamClient())
    target = tmp_path / "out.csv"

    with pytest.raises(StorageUnavailableError):
        await storage.download_to("k", target)

    assert not target.exists()


# --- FakeStorage contract --------------------------------------------------------------------


async def test_fake_storage_round_trips_a_file(tmp_path: Path) -> None:
    storage = FakeStorage()
    source = tmp_path / "bom.csv"
    source.write_bytes(b"a,b\n")
    target = tmp_path / "out.csv"

    await storage.put_file("k", source, "text/csv")
    await storage.download_to("k", target)

    assert target.read_bytes() == b"a,b\n"


async def test_fake_storage_missing_and_deleted_objects_raise_not_found(tmp_path: Path) -> None:
    storage = FakeStorage()
    source = tmp_path / "bom.csv"
    source.write_bytes(b"x")
    await storage.put_file("k", source, "text/csv")
    await storage.delete("k")

    with pytest.raises(StorageObjectNotFoundError):
        await storage.download_to("k", tmp_path / "out.csv")


async def test_fake_storage_presign_records_the_request_and_expiry() -> None:
    storage = FakeStorage(clock=lambda: NOW)

    _, expires_at = await storage.presigned_download_url("k", "bom.csv", "text/csv", 60)

    assert expires_at == NOW + timedelta(seconds=60)
    assert storage.presign_requests[0].content_disposition == content_disposition("bom.csv")


async def test_fake_storage_failure_injection_raises_storage_unavailable() -> None:
    storage = FakeStorage()
    storage.failing_operations.add("delete")

    with pytest.raises(StorageUnavailableError):
        await storage.delete("k")
    await storage.check_bucket()

    storage.unavailable = True
    with pytest.raises(StorageUnavailableError):
        await storage.check_bucket()
