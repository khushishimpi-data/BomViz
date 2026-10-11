"""The request-scoped session (`DbSessionDep`) behaves as one unit of work per request.

Each test mounts a throwaway route on `db_app`; no test-only routes exist in application code.
"""

from collections.abc import Awaitable, Callable
from http import HTTPStatus

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import DbSessionDep
from app.core.exceptions import AppError
from app.models import User

pytestmark = pytest.mark.integration

ROUTE = "/_test/unit-of-work"
FIRST_EMAIL = "first@example.com"
SECOND_EMAIL = "second@example.com"

Handler = Callable[[AsyncSession], Awaitable[None]]


def _mount(app: FastAPI, handler: Handler) -> None:
    async def endpoint(session: DbSessionDep) -> dict[str, str]:
        await handler(session)
        return {"status": "ok"}

    app.add_api_route(ROUTE, endpoint, methods=["POST"], status_code=HTTPStatus.CREATED)


def _new_user(email: str) -> User:
    return User(email=email, password_hash="not-a-real-hash", full_name="Unit Of Work")


async def _user_exists(session: AsyncSession, email: str) -> bool:
    count = await session.scalar(select(func.count(User.id)).where(User.email == email))
    return count == 1


async def test_successful_request_commits_its_work(
    db_app: FastAPI, db_client: AsyncClient, db_session: AsyncSession
) -> None:
    async def handler(session: AsyncSession) -> None:
        session.add(_new_user(FIRST_EMAIL))

    _mount(db_app, handler)

    response = await db_client.post(ROUTE)

    assert response.status_code == HTTPStatus.CREATED
    assert await _user_exists(db_session, FIRST_EMAIL)


async def test_request_raising_app_error_rolls_back_its_work(
    db_app: FastAPI, db_client: AsyncClient, db_session: AsyncSession
) -> None:
    async def handler(session: AsyncSession) -> None:
        session.add(_new_user(FIRST_EMAIL))
        await session.flush()  # the row reaches the database before the failure
        raise AppError("Conflict.", code="TEST_CONFLICT", status_code=HTTPStatus.CONFLICT)

    _mount(db_app, handler)

    response = await db_client.post(ROUTE)

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.json()["error"]["code"] == "TEST_CONFLICT"
    assert not await _user_exists(db_session, FIRST_EMAIL)


async def test_request_raising_unexpected_error_rolls_back_its_work(
    db_app: FastAPI, db_client: AsyncClient, db_session: AsyncSession
) -> None:
    async def handler(session: AsyncSession) -> None:
        session.add(_new_user(FIRST_EMAIL))
        await session.flush()
        raise RuntimeError("simulated bug")

    _mount(db_app, handler)

    response = await db_client.post(ROUTE)

    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert not await _user_exists(db_session, FIRST_EMAIL)


async def test_work_committed_before_raising_survives_the_failed_request(
    db_app: FastAPI, db_client: AsyncClient, db_session: AsyncSession
) -> None:
    # The failed-login pattern: persist the audit row, keep using the session, then fail.
    async def handler(session: AsyncSession) -> None:
        session.add(_new_user(FIRST_EMAIL))
        await session.commit()
        session.add(_new_user(SECOND_EMAIL))
        await session.flush()
        raise AppError("Denied.", code="TEST_DENIED", status_code=HTTPStatus.UNAUTHORIZED)

    _mount(db_app, handler)

    response = await db_client.post(ROUTE)

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.json()["error"]["code"] == "TEST_DENIED"
    assert await _user_exists(db_session, FIRST_EMAIL)
    assert not await _user_exists(db_session, SECOND_EMAIL)


async def test_failing_commit_returns_an_error_instead_of_success(
    db_app: FastAPI, db_client: AsyncClient, db_session: AsyncSession
) -> None:
    db_session.add(_new_user(FIRST_EMAIL))
    await db_session.commit()

    async def handler(session: AsyncSession) -> None:
        # Not flushed, so the unique violation only surfaces when the dependency commits.
        session.add(_new_user(FIRST_EMAIL))

    _mount(db_app, handler)

    response = await db_client.post(ROUTE)

    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
