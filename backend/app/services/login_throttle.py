"""Progressive per-(email, IP) login throttling (spec amendment A).

The first `login_free_attempts - 1` failures carry no delay. From the `login_free_attempts`-th
failure on, the pair is blocked for `base * 2 ** (failure_count - free)` seconds, capped at
`login_max_delay_seconds`. Counters untouched for `login_throttle_reset_hours` count as zero.
"""

import hashlib
import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.repositories.login_throttle_repository import LoginThrottleRepository


def delay_seconds(failure_count: int, settings: Settings) -> int:
    """Block duration after the `failure_count`-th consecutive failure (0 = not blocked)."""
    exponent = failure_count - settings.login_free_attempts
    base = settings.login_base_delay_seconds
    cap = settings.login_max_delay_seconds
    if exponent < 0 or base <= 0:
        return 0
    if exponent >= cap.bit_length():
        # base * 2**exponent >= 2**exponent > cap: skip building a huge integer.
        return cap
    return int(min(base * 2**exponent, cap))


def throttle_key(email: str, ip: str) -> str:
    """SHA-256 hex identifying one (email, IP) pair; the raw values are never stored."""
    material = f"{email.strip().lower()}|{ip}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class FailureOutcome:
    """Result of counting one failed login."""

    failure_count: int
    # Seconds the pair is now blocked for; 0 when this failure did not start a block.
    delay_seconds: int


class LoginThrottleService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._repository = LoginThrottleRepository(session)
        self._settings = settings

    def _stale_before(self, now: datetime) -> datetime:
        return now - timedelta(hours=self._settings.login_throttle_reset_hours)

    async def retry_after_seconds(self, key_hash: str, now: datetime) -> int | None:
        """Lock the pair and return the seconds left on an active block, else None.

        The advisory lock is held until the caller's transaction ends, so concurrent attempts
        on one pair run block check -> password check -> `record_failure` one at a time. The
        caller must therefore commit (or roll back) only after recording the outcome.
        """
        await self._repository.lock_pair(key_hash)
        row = await self._repository.get_for_update(key_hash)
        if row is None or row.last_failure_at < self._stale_before(now):
            return None
        if row.blocked_until is None or row.blocked_until <= now:
            return None
        return max(1, math.ceil((row.blocked_until - now).total_seconds()))

    async def record_failure(self, key_hash: str, now: datetime) -> FailureOutcome:
        """Count a failure and, once past the free attempts, block the pair."""
        failure_count = await self._repository.record_failure(
            key_hash, now, self._stale_before(now)
        )
        delay = delay_seconds(failure_count, self._settings)
        if delay > 0:
            await self._repository.set_blocked_until(key_hash, now + timedelta(seconds=delay))
        return FailureOutcome(failure_count=failure_count, delay_seconds=delay)

    async def clear(self, key_hash: str) -> None:
        """Forget the pair's failures (after a successful login)."""
        await self._repository.delete(key_hash)
