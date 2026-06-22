"""initial_schema

Revision ID: 9014f72e9f0c
Revises:
Create Date: 2026-06-22 21:41:24.005007

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "9014f72e9f0c"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Enum types ────────────────────────────────────────────────────────────
    # Only 3 of the 11 enums in enums.py are used by current ORM models.
    # The remaining 8 will be created in Phase 1+ migrations when their tables
    # are added.
    op.execute(sa.text("CREATE TYPE user_role_enum AS ENUM ('admin', 'inward_operator', 'packer')"))
    op.execute(sa.text("CREATE TYPE audit_module_enum AS ENUM ('shared', 'inward', 'outward', 'reports')"))
    op.execute(sa.text("CREATE TYPE customer_status_enum AS ENUM ('active', 'inactive')"))

    # ── Tables (dependency order: no-FK tables first) ─────────────────────────

    op.create_table(
        "organisations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("full_name", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        # ── Brute-force protection (June 18 auth redesign) ───────────────────
        sa.Column("failed_attempts", sa.Integer(), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        # ── NOT included: tokens_invalidated_at (superseded design) ──────────
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )

    # user_organisations: composite PK (user_id, organisation_id), no id column
    op.create_table(
        "user_organisations",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("organisation_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["organisation_id"], ["organisations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "organisation_id"),
    )
    op.create_index("idx_user_orgs_org_id", "user_organisations", ["organisation_id"])

    # user_roles: per-org role assignment (hard-delete only — no soft delete)
    op.create_table(
        "user_roles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("organisation_id", sa.Integer(), nullable=False),
        sa.Column(
            "role",
            sa.Enum("admin", "inward_operator", "packer", name="user_role_enum", create_type=False),
            nullable=False,
        ),
        sa.Column("assigned_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["assigned_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["organisation_id"], ["organisations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "organisation_id", "role", name="uq_user_roles"),
    )
    op.create_index("idx_user_roles_org_id", "user_roles", ["organisation_id"])

    # sessions: rotating refresh token store (June 18 redesign)
    # Raw refresh token is NEVER stored — only HMAC-SHA256 hash.
    # No TimestampMixin: has created_at + last_used_at but NO updated_at.
    op.create_table(
        "sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("refresh_token_hash", sa.Text(), nullable=False),
        sa.Column("previous_refresh_token_hash", sa.Text(), nullable=True),
        sa.Column("previous_token_valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("organisation_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.Integer(), nullable=True),
        sa.Column("revoke_reason", sa.Text(), nullable=True),
        sa.Column("ip_address", postgresql.INET(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["organisation_id"], ["organisations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["revoked_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("refresh_token_hash", name="uq_sessions_refresh_token_hash"),
    )
    op.create_index(
        "idx_sessions_previous_refresh_token_hash",
        "sessions",
        ["previous_refresh_token_hash"],
        postgresql_where=sa.text("previous_refresh_token_hash IS NOT NULL"),
    )
    op.create_index("idx_sessions_user_id", "sessions", ["user_id"])
    op.create_index(
        "idx_sessions_user_active",
        "sessions",
        ["user_id", "expires_at"],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )

    # login_attempts: per-IP sliding-window rate limit — no Redis required
    # Composite PK on (ip_address, window_start) — the upsert key.
    op.create_table(
        "login_attempts",
        sa.Column("ip_address", postgresql.INET(), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("ip_address", "window_start"),
    )

    # audit_logs: append-only — no UPDATE or DELETE ever touches this table.
    # BIGSERIAL PK to accommodate large volumes across all modules.
    # organisation_id FK has no ondelete — defaults to RESTRICT (desired: orgs
    # should not be deletable while audit history references them).
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("organisation_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column(
            "module",
            sa.Enum("shared", "inward", "outward", "reports", name="audit_module_enum", create_type=False),
            nullable=False,
        ),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("resource_type", sa.Text(), nullable=False),
        sa.Column("resource_id", sa.BigInteger(), nullable=True),
        sa.Column("before_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("ip_address", postgresql.INET(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["organisation_id"], ["organisations.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_audit_org_created", "audit_logs", ["organisation_id", "created_at"])
    op.create_index("idx_audit_resource", "audit_logs", ["resource_type", "resource_id"])
    op.create_index("idx_audit_user", "audit_logs", ["user_id", "created_at"])

    # customers: stub for Phase 1 — columns only, no business logic yet
    op.create_table(
        "customers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organisation_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("active", "inactive", name="customer_status_enum", create_type=False),
            nullable=False,
        ),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["organisation_id"], ["organisations.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organisation_id", "code", name="uq_customers_org_code"),
    )


def downgrade() -> None:
    # Drop tables in reverse dependency order. Dropping a table automatically
    # drops all indexes on it — explicit index drops are included only for the
    # partial indexes that Alembic cannot infer from table metadata alone.
    op.drop_table("customers")

    op.drop_index("idx_audit_user", table_name="audit_logs")
    op.drop_index("idx_audit_resource", table_name="audit_logs")
    op.drop_index("idx_audit_org_created", table_name="audit_logs")
    op.drop_table("audit_logs")

    op.drop_table("login_attempts")

    op.drop_index("idx_sessions_user_active", table_name="sessions")
    op.drop_index("idx_sessions_user_id", table_name="sessions")
    op.drop_index("idx_sessions_previous_refresh_token_hash", table_name="sessions")
    op.drop_table("sessions")

    op.drop_index("idx_user_roles_org_id", table_name="user_roles")
    op.drop_table("user_roles")

    op.drop_index("idx_user_orgs_org_id", table_name="user_organisations")
    op.drop_table("user_organisations")

    op.drop_table("users")
    op.drop_table("organisations")

    # Drop enum types last (tables that reference them are already gone)
    op.execute(sa.text("DROP TYPE customer_status_enum"))
    op.execute(sa.text("DROP TYPE audit_module_enum"))
    op.execute(sa.text("DROP TYPE user_role_enum"))
