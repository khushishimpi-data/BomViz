import logging

import pytest

from app.services.rate_limiter import RateLimitedError, RateLimiter
from tests.conftest import make_settings
from tests.fakes import FailingRedis, FakeClock, FakeRedis

BUCKET = "login"
IDENTITY = "203.0.113.7"
LIMIT = 3
WINDOW_SECONDS = 900


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def redis(clock: FakeClock) -> FakeRedis:
    return FakeRedis(clock=clock)


@pytest.fixture
def limiter(redis: FakeRedis) -> RateLimiter:
    return RateLimiter(redis, make_settings(rate_limit_enabled=True))


async def _hit(limiter: RateLimiter, identity: str = IDENTITY) -> None:
    await limiter.hit(BUCKET, identity, LIMIT, WINDOW_SECONDS)


async def test_allows_attempts_up_to_the_limit(limiter: RateLimiter) -> None:
    for _ in range(LIMIT):
        await _hit(limiter)


async def test_rejects_the_attempt_after_the_limit_with_retry_after(
    limiter: RateLimiter, clock: FakeClock
) -> None:
    for _ in range(LIMIT):
        await _hit(limiter)
    clock.advance(100)

    with pytest.raises(RateLimitedError) as excinfo:
        await _hit(limiter)

    error = excinfo.value
    assert error.status_code == 429
    assert error.code == "RATE_LIMITED"
    assert error.message == "Too many attempts. Please wait and try again."
    assert error.retry_after == WINDOW_SECONDS - 100
    assert error.headers == {"Retry-After": str(WINDOW_SECONDS - 100)}


async def test_retry_after_is_at_least_one_second(limiter: RateLimiter, clock: FakeClock) -> None:
    for _ in range(LIMIT):
        await _hit(limiter)
    clock.advance(WINDOW_SECONDS - 0.4)  # Redis reports TTL 0

    with pytest.raises(RateLimitedError) as excinfo:
        await _hit(limiter)

    assert excinfo.value.retry_after == 1


async def test_later_hits_do_not_extend_the_window(limiter: RateLimiter, clock: FakeClock) -> None:
    await _hit(limiter)
    clock.advance(WINDOW_SECONDS - 1)
    for _ in range(LIMIT - 1):
        await _hit(limiter)

    with pytest.raises(RateLimitedError) as excinfo:
        await _hit(limiter)

    assert excinfo.value.retry_after == 1


async def test_counter_resets_when_the_window_expires(
    limiter: RateLimiter, clock: FakeClock
) -> None:
    for _ in range(LIMIT):
        await _hit(limiter)
    clock.advance(WINDOW_SECONDS)

    for _ in range(LIMIT):
        await _hit(limiter)


async def test_identities_are_counted_separately(limiter: RateLimiter) -> None:
    for _ in range(LIMIT):
        await _hit(limiter, identity="198.51.100.1")

    await _hit(limiter, identity="198.51.100.2")


async def test_buckets_are_counted_separately(limiter: RateLimiter) -> None:
    for _ in range(LIMIT):
        await limiter.hit("register", IDENTITY, LIMIT, WINDOW_SECONDS)

    await _hit(limiter)


async def test_identity_is_hashed_in_the_redis_key(limiter: RateLimiter, redis: FakeRedis) -> None:
    await _hit(limiter, identity="person@example.com")

    [key] = redis.keys()
    assert key.startswith(f"rl:{BUCKET}:")
    assert "person@example.com" not in key


async def test_disabled_limiter_never_rejects_or_touches_redis(redis: FakeRedis) -> None:
    limiter = RateLimiter(redis, make_settings(rate_limit_enabled=False))

    for _ in range(LIMIT * 3):
        await _hit(limiter)

    assert redis.keys() == []


async def test_redis_failure_allows_the_request_and_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    limiter = RateLimiter(FailingRedis(), make_settings(rate_limit_enabled=True))

    with caplog.at_level(logging.WARNING, logger="app.services.rate_limiter"):
        for _ in range(LIMIT + 1):
            await _hit(limiter)

    messages = [record.getMessage() for record in caplog.records]
    assert messages == ["rate_limiter_unavailable"] * (LIMIT + 1)
    assert all(record.levelno == logging.WARNING for record in caplog.records)
    assert IDENTITY not in caplog.text
