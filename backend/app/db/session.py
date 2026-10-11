"""Async database engine and session factory construction, and connectivity checks."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings


def build_engine(settings: Settings) -> AsyncEngine:
    """Create the application's pooled async engine."""
    return create_async_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
    )


def build_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create the session factory bound to the application's engine.

    `expire_on_commit=False` keeps loaded attributes usable after commit, so a service can
    commit and still build its response from the ORM objects without lazy-loading (which
    async sessions cannot do implicitly).
    """
    return async_sessionmaker(engine, expire_on_commit=False)


async def ping_database(engine: AsyncEngine) -> None:
    """Raise if the database cannot execute a trivial query."""
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))
