"""Readiness checks for the backend's runtime dependencies."""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping

logger = logging.getLogger(__name__)

ReadinessCheck = Callable[[], Awaitable[object]]
"""A check passes when it returns and fails when it raises or times out."""


class HealthService:
    """Runs named dependency checks concurrently, each bounded by a timeout."""

    def __init__(self, checks: Mapping[str, ReadinessCheck], timeout_seconds: float) -> None:
        self._checks = checks
        self._timeout_seconds = timeout_seconds

    async def readiness(self) -> dict[str, bool]:
        """Return `{check_name: passed}` for every registered check."""
        names = list(self._checks)
        results = await asyncio.gather(*(self._run(name) for name in names))
        return dict(zip(names, results, strict=True))

    async def _run(self, name: str) -> bool:
        try:
            await asyncio.wait_for(self._checks[name](), timeout=self._timeout_seconds)
        except Exception:
            # Details go to the log only; the HTTP response exposes pass/fail per dependency.
            logger.warning("Readiness check failed: %s", name, exc_info=True)
            return False
        return True
