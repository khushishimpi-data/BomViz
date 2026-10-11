"""Object storage (S3 / MinIO) access. Credentials never leave the backend.

Every botocore failure is translated at this boundary: a missing object on download becomes
`StorageObjectNotFoundError` (internal), anything else becomes `StorageUnavailableError`
(503 `STORAGE_UNAVAILABLE` with a generic message; the detail is logged here, once).
"""

import asyncio
import logging
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import Settings
from app.core.exceptions import AppError
from app.services.upload_intake import clean_filename

logger = logging.getLogger(__name__)

MAX_DOWNLOAD_FILENAME_CHARS = 150
# SigV4 presigned URLs are valid for at most seven days.
MAX_PRESIGN_TTL_SECONDS = 7 * 24 * 60 * 60
_DOWNLOAD_CHUNK_BYTES = 1024 * 1024
_FALLBACK_FILENAME = "download"
_NOT_FOUND_CODES = frozenset({"NoSuchKey", "404", "NotFound"})


class StorageUnavailableError(AppError):
    """Object storage is unreachable or rejected the request."""

    status_code = HTTPStatus.SERVICE_UNAVAILABLE
    code = "STORAGE_UNAVAILABLE"

    def __init__(self) -> None:
        super().__init__("File storage is temporarily unavailable. Please try again later.")


class StorageObjectNotFoundError(Exception):
    """The requested object does not exist. Internal: callers decide what the user sees."""

    def __init__(self, key: str) -> None:
        super().__init__(f"Object not found: {key}")
        self.key = key


class Storage(Protocol):
    """The async object-storage interface services depend on (see `tests.fakes.FakeStorage`)."""

    async def check_bucket(self) -> None: ...

    async def put_file(self, key: str, path: Path, content_type: str) -> None: ...

    async def download_to(self, key: str, path: Path) -> None: ...

    async def delete(self, key: str) -> None: ...

    async def presigned_download_url(
        self, key: str, filename: str, content_type: str, ttl_seconds: int
    ) -> tuple[str, datetime]: ...


def _safe_filename(filename: str) -> str:
    """The shared filename sanitizer, at most `MAX_DOWNLOAD_FILENAME_CHARS`, never empty."""
    return clean_filename(filename, MAX_DOWNLOAD_FILENAME_CHARS) or _FALLBACK_FILENAME


def content_disposition(filename: str) -> str:
    """`attachment` disposition with an ASCII fallback and an RFC 5987 UTF-8 `filename*`."""
    name = _safe_filename(filename)
    fallback = "".join(ch if ch.isascii() else "_" for ch in name)
    fallback = fallback.replace('"', "").replace("\\", "") or _FALLBACK_FILENAME
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(name, safe='')}"


def _build_client(settings: Settings, endpoint_url: str | None) -> Any:
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url or None,
        aws_access_key_id=settings.s3_access_key_id.get_secret_value() or None,
        aws_secret_access_key=settings.s3_secret_access_key.get_secret_value() or None,
        region_name=settings.s3_region,
        config=BotoConfig(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            connect_timeout=2,
            read_timeout=10,
            retries={"max_attempts": 2, "mode": "standard"},
        ),
    )


def _unavailable(operation: str, exc: Exception) -> StorageUnavailableError:
    error_code = exc.response.get("Error", {}).get("Code") if isinstance(exc, ClientError) else None
    logger.warning(
        "Object storage %s failed (%s %s)",
        operation,
        type(exc).__name__,
        error_code or "",
        exc_info=exc,
    )
    return StorageUnavailableError()


class ObjectStorage:
    """Thin async wrapper over a boto3 S3 client bound to the configured bucket.

    Presigned URLs are signed by a second client bound to `S3_PUBLIC_ENDPOINT_URL` (falling back
    to the internal endpoint): SigV4 signs the host, so it must be the one the browser uses.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        client: Any | None = None,
        presign_client: Any | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._bucket = settings.s3_bucket
        self._client = client or _build_client(settings, settings.s3_endpoint_url)
        self._presign_client = presign_client or _build_client(
            settings, settings.s3_public_endpoint_url or settings.s3_endpoint_url
        )
        self._clock = clock or (lambda: datetime.now(UTC))

    async def check_bucket(self) -> None:
        """Raise if the bucket is unreachable or the credentials are rejected."""
        await asyncio.to_thread(self._client.head_bucket, Bucket=self._bucket)

    async def put_file(self, key: str, path: Path, content_type: str) -> None:
        """Upload a local file as `key` (single PUT; uploads are capped well below 5 GB)."""
        try:
            await asyncio.to_thread(self._put_file_sync, key, path, content_type)
        except (BotoCoreError, ClientError) as exc:
            raise _unavailable("put", exc) from exc

    def _put_file_sync(self, key: str, path: Path, content_type: str) -> None:
        with path.open("rb") as body:
            self._client.put_object(
                Bucket=self._bucket, Key=key, Body=body, ContentType=content_type
            )

    async def download_to(self, key: str, path: Path) -> None:
        """Stream `key` into a local file. On failure no partial file is left behind."""
        try:
            await asyncio.to_thread(self._download_sync, key, path)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in _NOT_FOUND_CODES:
                raise StorageObjectNotFoundError(key) from exc
            raise _unavailable("download", exc) from exc
        except BotoCoreError as exc:
            raise _unavailable("download", exc) from exc

    def _download_sync(self, key: str, path: Path) -> None:
        response = self._client.get_object(Bucket=self._bucket, Key=key)
        body = response["Body"]
        try:
            with path.open("wb") as target:
                for chunk in body.iter_chunks(_DOWNLOAD_CHUNK_BYTES):
                    target.write(chunk)
        except BaseException:
            with suppress(FileNotFoundError):
                path.unlink()
            raise
        finally:
            body.close()

    async def delete(self, key: str) -> None:
        """Delete `key`. Deleting a missing object succeeds (S3 semantics)."""
        try:
            await asyncio.to_thread(self._client.delete_object, Bucket=self._bucket, Key=key)
        except (BotoCoreError, ClientError) as exc:
            raise _unavailable("delete", exc) from exc

    async def presigned_download_url(
        self, key: str, filename: str, content_type: str, ttl_seconds: int
    ) -> tuple[str, datetime]:
        """A time-limited GET URL that downloads `key` as `filename`, and its UTC expiry."""
        if not 1 <= ttl_seconds <= MAX_PRESIGN_TTL_SECONDS:
            raise ValueError(f"ttl_seconds must be within 1..{MAX_PRESIGN_TTL_SECONDS}")
        expires_at = self._clock() + timedelta(seconds=ttl_seconds)
        params = {
            "Bucket": self._bucket,
            "Key": key,
            "ResponseContentDisposition": content_disposition(filename),
            "ResponseContentType": content_type,
        }
        try:
            url: str = await asyncio.to_thread(
                self._presign_client.generate_presigned_url,
                "get_object",
                Params=params,
                ExpiresIn=ttl_seconds,
            )
        except (BotoCoreError, ClientError) as exc:
            raise _unavailable("presign", exc) from exc
        return url, expires_at
