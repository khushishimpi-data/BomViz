import asyncio

from app.services.health_service import HealthService


async def _passing() -> None:
    return None


async def _failing() -> None:
    raise RuntimeError("down")


async def _hanging() -> None:
    await asyncio.sleep(10)


async def test_reports_each_check_independently() -> None:
    service = HealthService({"a": _passing, "b": _failing}, timeout_seconds=1)

    assert await service.readiness() == {"a": True, "b": False}


async def test_check_exceeding_timeout_fails() -> None:
    service = HealthService({"slow": _hanging}, timeout_seconds=0.05)

    assert await service.readiness() == {"slow": False}
