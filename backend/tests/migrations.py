"""Alembic helpers shared by the integration fixtures and the migration tests."""

import asyncio
from pathlib import Path
from typing import Literal

from alembic import command
from alembic.config import Config

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def alembic_config(database_url: str) -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    config.attributes["configure_logger"] = False
    return config


async def run_alembic(
    database_url: str, operation: Literal["upgrade", "downgrade"], target: str
) -> None:
    # Alembic's env.py runs its own event loop, so drive it from a worker thread.
    upgrade_or_downgrade = command.upgrade if operation == "upgrade" else command.downgrade
    await asyncio.to_thread(upgrade_or_downgrade, alembic_config(database_url), target)
