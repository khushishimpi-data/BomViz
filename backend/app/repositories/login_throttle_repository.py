"""Queries on `login_throttles`, the per-(email, IP) failed-login counters.

Rows are addressed only by `key_hash`; the raw email and IP never reach this layer.
"""

from datetime import datetime

from sqlalchemy import case, delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LoginThrottle


class LoginThrottleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def lock_pair(self, key_hash: str) -> None:
        """Take a transaction-scoped advisory lock on the pair, released at commit/rollback.

        Unlike `get_for_update`, this also serializes attempts on a pair that has no row yet,
        so concurrent first attempts cannot all pass the block check before any is counted.
        """
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key_hash, 0))"),
            {"key_hash": key_hash},
        )

    async def get_for_update(self, key_hash: str) -> LoginThrottle | None:
        """Load and row-lock the counter (`SELECT ... FOR UPDATE`), bypassing stale cache."""
        result = await self._session.execute(
            select(LoginThrottle)
            .where(LoginThrottle.key_hash == key_hash)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def record_failure(self, key_hash: str, now: datetime, stale_before: datetime) -> int:
        """Count one failure for `key_hash` and return the new `failure_count`.

        A single `INSERT ... ON CONFLICT DO UPDATE`, so concurrent first failures cannot lose
        an increment. A row whose `last_failure_at` is older than `stale_before` restarts at 1.
        Any previous block is cleared; the caller sets a new one with `set_blocked_until`.
        """
        values = insert(LoginThrottle).values(
            key_hash=key_hash,
            failure_count=1,
            last_failure_at=now,
            blocked_until=None,
            updated_at=func.now(),
        )
        statement = values.on_conflict_do_update(
            index_elements=[LoginThrottle.key_hash],
            set_={
                "failure_count": case(
                    (LoginThrottle.last_failure_at < stale_before, 1),
                    else_=LoginThrottle.failure_count + 1,
                ),
                "last_failure_at": now,
                "blocked_until": None,
                # ORM `onupdate` does not apply to Core upserts, so set it here.
                "updated_at": func.now(),
            },
        ).returning(LoginThrottle.failure_count)
        result = await self._session.execute(statement)
        return int(result.scalar_one())

    async def set_blocked_until(self, key_hash: str, blocked_until: datetime) -> None:
        await self._session.execute(
            update(LoginThrottle)
            .where(LoginThrottle.key_hash == key_hash)
            .values(blocked_until=blocked_until, updated_at=func.now())
        )

    async def delete(self, key_hash: str) -> None:
        await self._session.execute(delete(LoginThrottle).where(LoginThrottle.key_hash == key_hash))
