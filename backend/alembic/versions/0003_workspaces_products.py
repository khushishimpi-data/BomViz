"""Workspaces, memberships, products, revisions and the audit log.

- enum types user_role, product_status, revision_status
- workspaces: citext slug (unique), owner_id -> users (RESTRICT)
- workspace_members: per-workspace role, UNIQUE (workspace_id, user_id)
- products: UNIQUE (workspace_id, product_code); product_revisions: UNIQUE (product_id,
  revision_code), scoped through the parent product (deviation A11)
- audit_logs: workspace_id ON DELETE RESTRICT so history is never cascaded away

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

user_role = postgresql.ENUM(
    "ADMIN", "ENGINEER", "PROCUREMENT", "PRODUCTION", "VIEWER", name="user_role", create_type=False
)
product_status = postgresql.ENUM(
    "DRAFT", "ACTIVE", "OBSOLETE", name="product_status", create_type=False
)
revision_status = postgresql.ENUM(
    "DRAFT", "RELEASED", "OBSOLETE", name="revision_status", create_type=False
)


def _timestamps() -> list[sa.Column[object]]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
    ]


def upgrade() -> None:
    bind = op.get_bind()
    user_role.create(bind)
    product_status.create(bind)
    revision_status.create(bind)

    op.create_table(
        "workspaces",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("slug", postgresql.CITEXT(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspaces")),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], name=op.f("fk_workspaces_owner_id_users"), ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("slug", name=op.f("uq_workspaces_slug")),
    )
    op.create_index(op.f("ix_workspaces_owner_id"), "workspaces", ["owner_id"])

    op.create_table(
        "workspace_members",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", user_role, nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspace_members")),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_workspace_members_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_workspace_members_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "workspace_id", "user_id", name=op.f("uq_workspace_members_workspace_id_user_id")
        ),
    )
    op.create_index(op.f("ix_workspace_members_user_id"), "workspace_members", ["user_id"])

    op.create_table(
        "products",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("product_code", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "status",
            product_status,
            server_default=sa.text("'DRAFT'::product_status"),
            nullable=False,
        ),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_products")),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_products_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name=op.f("fk_products_created_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "workspace_id", "product_code", name=op.f("uq_products_workspace_id_product_code")
        ),
    )
    op.create_index(op.f("ix_products_workspace_id"), "products", ["workspace_id"])

    op.create_table(
        "product_revisions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("revision_code", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "status",
            revision_status,
            server_default=sa.text("'DRAFT'::revision_status"),
            nullable=False,
        ),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_product_revisions")),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_product_revisions_product_id_products"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name=op.f("fk_product_revisions_created_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "product_id", "revision_code", name=op.f("uq_product_revisions_product_id_revision_code")
        ),
    )
    op.create_index(op.f("ix_product_revisions_product_id"), "product_revisions", ["product_id"])

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=True),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=True),
        sa.Column(
            "action_metadata",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_logs")),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_audit_logs_workspace_id_workspaces"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"], ["users.id"], name=op.f("fk_audit_logs_actor_id_users"), ondelete="SET NULL"
        ),
    )
    op.create_index(
        "ix_audit_logs_workspace_id_created_at",
        "audit_logs",
        ["workspace_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_audit_logs_actor_id_created_at",
        "audit_logs",
        ["actor_id", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_audit_logs_actor_id_created_at", table_name="audit_logs")
    op.drop_index("ix_audit_logs_workspace_id_created_at", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_index(op.f("ix_product_revisions_product_id"), table_name="product_revisions")
    op.drop_table("product_revisions")
    op.drop_index(op.f("ix_products_workspace_id"), table_name="products")
    op.drop_table("products")
    op.drop_index(op.f("ix_workspace_members_user_id"), table_name="workspace_members")
    op.drop_table("workspace_members")
    op.drop_index(op.f("ix_workspaces_owner_id"), table_name="workspaces")
    op.drop_table("workspaces")

    bind = op.get_bind()
    revision_status.drop(bind)
    product_status.drop(bind)
    user_role.drop(bind)
