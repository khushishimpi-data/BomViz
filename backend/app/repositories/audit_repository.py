"""Writes to the append-only `audit_logs` table.

There are no read queries yet (audit-log read endpoints are out of scope for Phase 1); when they
are added they must filter by `workspace_id`.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, entry: AuditLog) -> AuditLog:
        """Insert `entry` in the session's current transaction.

        Flushing surfaces constraint failures (e.g. an unknown workspace) at the call site
        instead of at commit time; committing stays with the caller's unit of work.
        """
        self._session.add(entry)
        await self._session.flush()
        return entry
