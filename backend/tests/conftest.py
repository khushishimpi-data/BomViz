"""Shared fixtures.

Unit and API tests run without external services: settings are built explicitly (no `.env`)
and dependency checks are injected. Integration tests (`-m integration`) use a real
PostgreSQL from `TEST_DATABASE_URL` or, when unset, a Testcontainers instance.

Database-backed tests use the `db_*` fixtures. Each of them pulls in `truncate_tables`, so
every such test starts and ends with empty application tables; unit tests never touch the
database.
"""

import os
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

# Modules that build process-wide singletons at import time (the Celery app) need a
# database URL; tests never connect to it.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://unused:unused@localhost:1/unused")

import app.models as _models  # noqa: F401 - registers every model on Base.metadata
from app.core.config import Settings
from app.db.base import Base
from app.db.session import build_sessionmaker
from app.main import create_app
from tests.migrations import run_alembic


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "app_env": "test",
        "log_format": "json",
        "database_url": "postgresql+asyncpg://unused:unused@localhost:1/unused",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http_client:
        yield http_client


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """Async SQLAlchemy URL of a disposable PostgreSQL database."""
    configured = os.environ.get("TEST_DATABASE_URL")
    if configured:
        yield configured
        return

    from testcontainers.community.postgres import PostgresContainer  # noqa: PLC0415 - optional, slow import

    with PostgresContainer("postgres:16-alpine", driver="asyncpg") as container:
        yield container.get_connection_url()


@pytest.fixture(scope="session")
async def migrated_database_url(database_url: str) -> str:
    """`database_url` upgraded to the latest migration, once per test session."""
    await run_alembic(database_url, "upgrade", "head")
    return database_url


async def _truncate_application_tables(database_url: str) -> None:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            quote = connection.dialect.identifier_preparer.quote
            table_names = ", ".join(quote(table.name) for table in Base.metadata.sorted_tables)
            await connection.execute(text(f"TRUNCATE TABLE {table_names} RESTART IDENTITY CASCADE"))
    finally:
        await engine.dispose()


@pytest.fixture
async def truncate_tables(migrated_database_url: str) -> AsyncIterator[None]:
    """Empty every application table before and after the test.

    Truncating up front as well keeps a test independent of rows left behind by an aborted
    earlier run against a persistent `TEST_DATABASE_URL`.
    """
    await _truncate_application_tables(migrated_database_url)
    yield
    await _truncate_application_tables(migrated_database_url)


@pytest.fixture
def db_settings(migrated_database_url: str, truncate_tables: None) -> Settings:
    # Rate limiting needs Redis; tests that exercise it override this fixture.
    return make_settings(database_url=migrated_database_url, rate_limit_enabled=False)


@pytest.fixture
def db_app(db_settings: Settings) -> FastAPI:
    return create_app(db_settings)


@pytest.fixture
async def db_client(db_app: FastAPI) -> AsyncIterator[AsyncClient]:
    """HTTP client for `db_app` with its lifespan running (engine, sessionmaker, clients)."""
    async with db_app.router.lifespan_context(db_app):
        transport = ASGITransport(app=db_app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as http_client:
            yield http_client


@pytest.fixture
async def db_session(
    migrated_database_url: str, truncate_tables: None
) -> AsyncIterator[AsyncSession]:
    """A session on the migrated database for arranging data and asserting on it directly."""
    engine = create_async_engine(migrated_database_url, poolclass=NullPool)
    try:
        async with build_sessionmaker(engine)() as session:
            yield session
    finally:
        await engine.dispose()
