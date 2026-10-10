import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.session import ping_database

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _alembic_config(database_url: str) -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    config.attributes["configure_logger"] = False
    return config


async def _installed_extensions(database_url: str) -> set[str]:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            rows = await connection.execute(text("SELECT extname FROM pg_extension"))
            return {row[0] for row in rows}
    finally:
        await engine.dispose()


async def test_ping_database_succeeds_against_postgres(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        await ping_database(engine)
    finally:
        await engine.dispose()


async def test_migrations_upgrade_downgrade_round_trip(database_url: str) -> None:
    config = _alembic_config(database_url)

    # Alembic's env.py runs its own event loop, so drive it from a worker thread.
    await asyncio.to_thread(command.upgrade, config, "head")
    assert {"citext", "pg_trgm"} <= await _installed_extensions(database_url)

    await asyncio.to_thread(command.downgrade, config, "base")
    assert not {"citext", "pg_trgm"} & await _installed_extensions(database_url)

    await asyncio.to_thread(command.upgrade, config, "head")
    assert {"citext", "pg_trgm"} <= await _installed_extensions(database_url)
