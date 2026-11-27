"""Production hardening: refresh-token sessions, carrier webhook deliveries, and an
append-only guard on audit_logs.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _uuid_pk() -> sa.Column:
    return sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()"))


def _ts(name: str, nullable: bool = True, default_now: bool = False) -> sa.Column:
    return sa.Column(
        name, sa.DateTime(timezone=True), nullable=nullable,
        server_default=sa.func.now() if default_now else None,
    )  # fmt: skip


def _fk(name: str, target: str, ondelete: str, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.Uuid(), sa.ForeignKey(target, ondelete=ondelete), nullable=nullable)


# Audit rows may only change through ON DELETE SET NULL on their foreign keys.
_AUDIT_GUARD = """
CREATE OR REPLACE FUNCTION audit_logs_append_only() RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'audit_logs is append-only';
  END IF;
  IF (NEW.id, NEW.action, NEW.entity_type, NEW.entity_id, NEW.occurred_at, NEW.ip_address,
      NEW.user_agent, NEW.changes)
     IS DISTINCT FROM
     (OLD.id, OLD.action, OLD.entity_type, OLD.entity_id, OLD.occurred_at, OLD.ip_address,
      OLD.user_agent, OLD.changes)
     OR (NEW.organization_id IS DISTINCT FROM OLD.organization_id AND NEW.organization_id IS NOT NULL)
     OR (NEW.actor_user_id IS DISTINCT FROM OLD.actor_user_id AND NEW.actor_user_id IS NOT NULL)
     OR (NEW.actor_api_key_id IS DISTINCT FROM OLD.actor_api_key_id AND NEW.actor_api_key_id IS NOT NULL)
  THEN
    RAISE EXCEPTION 'audit_logs is append-only';
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.create_table(
        "auth_sessions",
        _uuid_pk(),
        _fk("user_id", "users.id", "CASCADE"),
        _fk("organization_id", "organizations.id", "CASCADE"),
        # SHA-256 of the opaque refresh token; rotated on every refresh.
        sa.Column("refresh_hash", sa.String(64), nullable=False, unique=True),
        _ts("created_at", nullable=False, default_now=True),
        _ts("last_used_at"),
        _ts("expires_at", nullable=False),
        _ts("revoked_at"),
        sa.Column("ip_address", pg.INET()),
        sa.Column("user_agent", sa.Text()),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])

    op.create_table(
        "webhook_deliveries",
        _uuid_pk(),
        _fk("carrier_id", "carriers.id", "CASCADE"),
        sa.Column("delivery_id", sa.String(200), nullable=False),
        _ts("received_at", nullable=False, default_now=True),
        sa.Column("signed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("events_received", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text()),
        sa.UniqueConstraint("carrier_id", "delivery_id"),
    )
    op.create_index(
        "ix_webhook_deliveries_received_at", "webhook_deliveries", [sa.text("received_at DESC")]
    )

    op.execute(_AUDIT_GUARD)
    op.execute(
        "CREATE TRIGGER audit_logs_append_only BEFORE UPDATE OR DELETE ON audit_logs "
        "FOR EACH ROW EXECUTE FUNCTION audit_logs_append_only()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_logs_append_only ON audit_logs")
    op.execute("DROP FUNCTION IF EXISTS audit_logs_append_only()")
    op.drop_table("webhook_deliveries")
    op.drop_table("auth_sessions")
