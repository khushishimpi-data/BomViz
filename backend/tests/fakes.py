"""In-memory stand-ins for external services, with real (simplified) behaviour."""

import math
from collections.abc import Callable
from types import TracebackType
from typing import Any, Self

from redis.exceptions import ConnectionError as RedisConnectionError


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
