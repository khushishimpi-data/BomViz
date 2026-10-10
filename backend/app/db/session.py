"""Async database engine construction and connectivity checks."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.config import Settings


def build_engine(settings: Settings) -> AsyncEngine:
    """Create the application's pooled async engine."""
    return create_async_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
    )


async def ping_database(engine: AsyncEngine) -> None:
    """Raise if the database cannot execute a trivial query."""
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))
