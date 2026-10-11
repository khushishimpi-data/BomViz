"""Fixed-window rate limiting backed by Redis.

Each `(bucket, identity)` pair gets one counter per window: the first hit creates the key and
starts its expiry, later hits only increment it. The identity (an IP address, possibly an email)
is SHA-256 hashed into the key, so Redis never stores it in the clear.

The limiter fails open: when Redis is unreachable the request is allowed and a WARNING is
logged, trading strictness for availability.
"""

import hashlib
import logging
from http import HTTPStatus
from types import TracebackType
from typing import Any, Protocol, Self

from redis.exceptions import RedisError

from app.core.config import Settings
from app.core.exceptions import AppError

logger = logging.getLogger(__name__)

RATE_LIMITED_MESSAGE = "Too many attempts. Please wait and try again."


class RateLimitPipeline(Protocol):
    """The subset of a Redis transactional pipeline the limiter uses."""

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def incr(self, name: str) -> Any: ...

    def expire(self, name: str, time: int, nx: bool = ...) -> Any: ...

    def ttl(self, name: str) -> Any: ...

    async def execute(self) -> list[Any]: ...


class RateLimitStore(Protocol):
    """The subset of `redis.asyncio.Redis` the limiter uses."""

    def pipeline(self, transaction: bool = ...) -> RateLimitPipeline: ...


class RateLimitedError(AppError):
    """Raised when a caller exceeds a rate limit; carries the seconds until the window resets."""

    status_code = HTTPStatus.TOO_MANY_REQUESTS
    code = "RATE_LIMITED"

    def __init__(self, retry_after: int) -> None:
        super().__init__(RATE_LIMITED_MESSAGE, headers={"Retry-After": str(retry_after)})
        self.retry_after = retry_after


def rate_limit_key(bucket: str, identity: str) -> str:
    """Redis key for one caller's counter in one bucket; the identity is hashed."""
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return f"rl:{bucket}:{digest}"


class RateLimiter:
    def __init__(self, redis: RateLimitStore, settings: Settings) -> None:
        self._redis = redis
        self._enabled = settings.rate_limit_enabled

    async def hit(self, bucket: str, identity: str, limit: int, window_seconds: int) -> None:
        """Count one attempt; raise `RateLimitedError` once `limit` is exceeded in the window."""
        if not self._enabled:
            return

        key = rate_limit_key(bucket, identity)
        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.incr(key)
                pipe.expire(key, window_seconds, nx=True)
                pipe.ttl(key)
                count, _, ttl = await pipe.execute()
        except (RedisError, OSError, TimeoutError):
            # Fail open. The identity is deliberately left out of the log.
            logger.warning("rate_limiter_unavailable", extra={"bucket": bucket}, exc_info=True)
            return

        if int(count) > limit:
            raise RateLimitedError(retry_after=max(int(ttl), 1))
