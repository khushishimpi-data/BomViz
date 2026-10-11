"""In-memory stand-ins for external services, with real (simplified) behaviour."""

import asyncio
import math
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import TracebackType
from typing import Any, Self
from urllib.parse import quote

from redis.exceptions import ConnectionError as RedisConnectionError

from app.services.storage_service import (
    MAX_PRESIGN_TTL_SECONDS,
    StorageObjectNotFoundError,
    StorageUnavailableError,
    content_disposition,
)


class FakeClock:
    """A monotonic clock tests advance explicitly."""

    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeRedis:
    """Implements the Redis commands the rate limiter uses: INCR, EXPIRE (NX), TTL, pipelines.

    Expiry follows Redis semantics against an injectable clock: an expired key reads as absent,
    `ttl` returns -2 for a missing key, -1 for a key without an expiry, otherwise the remaining
    seconds rounded to the nearest second.
    """

    def __init__(self, clock: Callable[[], float] | None = None) -> None:
        self._clock = clock or FakeClock()
        self._values: dict[str, int] = {}
        self._expires_at: dict[str, float] = {}

    def _purge_if_expired(self, name: str) -> None:
        expires_at = self._expires_at.get(name)
        if expires_at is not None and expires_at <= self._clock():
            self._values.pop(name, None)
            self._expires_at.pop(name, None)

    async def incr(self, name: str) -> int:
        self._purge_if_expired(name)
        self._values[name] = self._values.get(name, 0) + 1
        return self._values[name]

    async def expire(self, name: str, time: int, nx: bool = False) -> bool:
        self._purge_if_expired(name)
        if name not in self._values or (nx and name in self._expires_at):
            return False
        self._expires_at[name] = self._clock() + time
        return True

    async def ttl(self, name: str) -> int:
        self._purge_if_expired(name)
        if name not in self._values:
            return -2
        expires_at = self._expires_at.get(name)
        if expires_at is None:
            return -1
        # Redis rounds the remaining time to the nearest second, so it can report 0.
        return math.floor(expires_at - self._clock() + 0.5)

    def keys(self) -> list[str]:
        """Live keys (test inspection helper, not a Redis command)."""
        for name in list(self._values):
            self._purge_if_expired(name)
        return list(self._values)

    def pipeline(self, transaction: bool = True) -> "FakePipeline":
        return FakePipeline(self)


class FakePipeline:
    """Queues commands and runs them in order on `execute`, like a MULTI/EXEC pipeline."""

    def __init__(self, redis: FakeRedis) -> None:
        self._redis = redis
        self._queued: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._queued.clear()

    def incr(self, name: str) -> Self:
        self._queued.append(("incr", (name,), {}))
        return self

    def expire(self, name: str, time: int, nx: bool = False) -> Self:
        self._queued.append(("expire", (name, time), {"nx": nx}))
        return self

    def ttl(self, name: str) -> Self:
        self._queued.append(("ttl", (name,), {}))
        return self

    async def execute(self) -> list[Any]:
        results = [
            await getattr(self._redis, command)(*args, **kwargs)
            for command, args, kwargs in self._queued
        ]
        self._queued.clear()
        return results


class FailingRedis(FakeRedis):
    """A Redis whose every pipeline fails as if the server were unreachable."""

    def pipeline(self, transaction: bool = True) -> "FakePipeline":
        return _FailingPipeline(self)


class _FailingPipeline(FakePipeline):
    async def execute(self) -> list[Any]:
        raise RedisConnectionError("Error 111 connecting to redis:6379. Connection refused.")


@dataclass(frozen=True, slots=True)
class StoredObject:
    data: bytes
    content_type: str


@dataclass(frozen=True, slots=True)
class PresignRequest:
    key: str
    filename: str
    content_type: str
    ttl_seconds: int
    content_disposition: str


class FakeStorage:
    """In-memory `Storage`: same async interface and error types as `ObjectStorage`.

    Failure injection: set `unavailable = True` to make every operation raise
    `StorageUnavailableError`, or add operation names ("check_bucket", "put_file",
    "download_to", "delete", "presigned_download_url") to `failing_operations`.
    """

    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self.objects: dict[str, StoredObject] = {}
        self.presign_requests: list[PresignRequest] = []
        self.unavailable = False
        self.failing_operations: set[str] = set()
        self._clock = clock or (lambda: datetime.now(UTC))

    def _check(self, operation: str) -> None:
        if self.unavailable or operation in self.failing_operations:
            raise StorageUnavailableError()

    async def check_bucket(self) -> None:
        self._check("check_bucket")

    async def put_file(self, key: str, path: Path, content_type: str) -> None:
        self._check("put_file")
        data = await asyncio.to_thread(path.read_bytes)
        self.objects[key] = StoredObject(data, content_type)

    async def download_to(self, key: str, path: Path) -> None:
        self._check("download_to")
        stored = self.objects.get(key)
        if stored is None:
            raise StorageObjectNotFoundError(key)
        await asyncio.to_thread(path.write_bytes, stored.data)

    async def delete(self, key: str) -> None:
        self._check("delete")
        self.objects.pop(key, None)

    async def presigned_download_url(
        self, key: str, filename: str, content_type: str, ttl_seconds: int
    ) -> tuple[str, datetime]:
        if not 1 <= ttl_seconds <= MAX_PRESIGN_TTL_SECONDS:
            raise ValueError(f"ttl_seconds must be within 1..{MAX_PRESIGN_TTL_SECONDS}")
        self._check("presigned_download_url")
        disposition = content_disposition(filename)
        self.presign_requests.append(
            PresignRequest(key, filename, content_type, ttl_seconds, disposition)
        )
        url = (
            f"https://storage.test/bomviz/{quote(key)}"
            f"?response-content-disposition={quote(disposition, safe='')}"
            f"&X-Amz-Expires={ttl_seconds}"
        )
        return url, self._clock() + timedelta(seconds=ttl_seconds)


class RecordingJobQueue:
    """A `JobQueue` that records every enqueued job id and the task id it was sent with."""

    def __init__(self) -> None:
        self.parse_jobs: list[uuid.UUID] = []
        self.task_ids: dict[uuid.UUID, str] = {}

    def enqueue_parse(self, job_id: uuid.UUID, task_id: str) -> None:
        self.parse_jobs.append(job_id)
        self.task_ids[job_id] = task_id


class FailingJobQueue:
    """A `JobQueue` whose broker is unreachable: every enqueue raises."""

    def enqueue_parse(self, job_id: uuid.UUID, task_id: str) -> None:
        raise ConnectionError("Error 111 connecting to redis:6379. Connection refused.")
