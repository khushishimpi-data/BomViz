"""Shared fixtures.

Unit and API tests run without external services: settings are built explicitly (no `.env`)
and dependency checks are injected. Integration tests (`-m integration`) use a real
PostgreSQL from `TEST_DATABASE_URL` or, when unset, a Testcontainers instance.
"""

import os
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

# Modules that build process-wide singletons at import time (the Celery app) need a
# database URL; tests never connect to it.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://unused:unused@localhost:1/unused")

from app.core.config import Settings
from app.main import create_app


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
