"""Queries on `refresh_tokens`. Tokens are only ever looked up by their SHA-256 hash."""

import uuid
from datetime import datetime

from sqlalchemy import exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RefreshToken


class RefreshTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, token: RefreshToken) -> RefreshToken:
        """Insert `token` and flush so its server-generated `id` is available."""
        self._session.add(token)
        await self._session.flush()
        return token

    async def get_by_hash_for_update(self, token_hash: str) -> RefreshToken | None:
        """Load and row-lock the token (`SELECT ... FOR UPDATE`) so rotation is serialized."""
        result = await self._session.execute(
            select(RefreshToken)
            .where(RefreshToken.token_hash == token_hash)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def revoke_family(self, family_id: uuid.UUID, now: datetime) -> int:
        """Revoke every live token of one login session; returns the number revoked."""
        result = await self._session.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        return int(getattr(result, "rowcount", 0))

    async def revoke_all_for_user(self, user_id: uuid.UUID, now: datetime) -> int:
        """Revoke every live token of every session of `user_id`; returns the number revoked."""
        result = await self._session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        return int(getattr(result, "rowcount", 0))

    async def family_is_live(self, family_id: uuid.UUID, user_id: uuid.UUID, now: datetime) -> bool:
        """True when the user's session `family_id` still has a non-revoked, unexpired token."""
        live = await self._session.scalar(
            select(
                exists().where(
                    RefreshToken.family_id == family_id,
                    RefreshToken.user_id == user_id,
                    RefreshToken.revoked_at.is_(None),
                    RefreshToken.expires_at > now,
                )
            )
        )
        return bool(live)
