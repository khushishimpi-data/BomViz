"""Failed-login counters per (email, IP) pair.

`key_hash` is the SHA-256 hex of `lower(email) + "|" + ip`; the raw email and IP are never
stored. This is a keyed counter, not an entity, so it has no `id` or `created_at`.
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Integer, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class LoginThrottle(Base):
    __tablename__ = "login_throttles"
    __table_args__ = (CheckConstraint("failure_count >= 0", name="failure_count_non_negative"),)

    key_hash: Mapped[str] = mapped_column(Text, primary_key=True)
    failure_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    last_failure_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    blocked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=func.now()
    )
