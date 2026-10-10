"""Baseline: PostgreSQL extensions required by later migrations.

- citext: case-insensitive unique email addresses (0002_auth)
- pg_trgm: trigram indexes for part number / name search (0006_bom_items)

Revision ID: 0001
Revises:
Create Date: 2026-10-10
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
    op.execute("DROP EXTENSION IF EXISTS citext")
