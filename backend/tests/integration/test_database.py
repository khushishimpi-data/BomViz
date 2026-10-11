from typing import Any

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

import app.models  # noqa: F401 - registers every model on Base.metadata
from app.db.base import Base
from app.db.session import ping_database
from tests.migrations import run_alembic

pytestmark = pytest.mark.integration

PHASE_1_TABLES = {
    "users",
    "refresh_tokens",
    "login_throttles",
    "workspaces",
    "workspace_members",
    "products",
    "product_revisions",
    "audit_logs",
}
PHASE_1_ENUM_TYPES = {"user_role", "product_status", "revision_status"}
EXTENSIONS = {"citext", "pg_trgm"}


async def _names(database_url: str, query: str) -> set[str]:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            rows = await connection.execute(text(query))
            return {row[0] for row in rows}
    finally:
        await engine.dispose()


async def _installed_extensions(database_url: str) -> set[str]:
    return await _names(database_url, "SELECT extname FROM pg_extension")


async def _public_tables(database_url: str) -> set[str]:
    return await _names(
        database_url,
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
        "AND tablename <> 'alembic_version'",
    )


async def _enum_types(database_url: str) -> set[str]:
    return await _names(
        database_url,
        "SELECT t.typname FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace "
        "WHERE t.typtype = 'e' AND n.nspname = 'public'",
    )


async def _schema_state(database_url: str) -> tuple[set[str], set[str], set[str]]:
    return (
        await _public_tables(database_url),
        await _enum_types(database_url),
        await _installed_extensions(database_url),
    )


def _schema_diff(connection: Connection) -> list[Any]:
    context = MigrationContext.configure(
        connection,
        opts={"compare_type": True, "compare_server_default": True},
    )
    return list(compare_metadata(context, Base.metadata))


async def test_ping_database_succeeds_against_postgres(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        await ping_database(engine)
    finally:
        await engine.dispose()


async def test_migrations_upgrade_downgrade_round_trip(database_url: str) -> None:
    await run_alembic(database_url, "upgrade", "head")
    tables, enums, extensions = await _schema_state(database_url)
    assert tables == PHASE_1_TABLES
    assert enums == PHASE_1_ENUM_TYPES
    assert extensions >= EXTENSIONS

    try:
        await run_alembic(database_url, "downgrade", "base")
        tables, enums, extensions = await _schema_state(database_url)
        assert tables == set()
        assert enums == set()
        assert not extensions & EXTENSIONS
    finally:
        # The database is shared by the whole session: always leave it at head so the
        # integration fixtures (`migrated_database_url`) still find their tables.
        await run_alembic(database_url, "upgrade", "head")

    tables, enums, extensions = await _schema_state(database_url)
    assert tables == PHASE_1_TABLES
    assert enums == PHASE_1_ENUM_TYPES
    assert extensions >= EXTENSIONS


async def test_models_match_migrated_schema(database_url: str) -> None:
    await run_alembic(database_url, "upgrade", "head")

    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            diff = await connection.run_sync(_schema_diff)
    finally:
        await engine.dispose()

    assert diff == []
