"""Foundation: extensions, identity, data sources, carriers, ports, alerts, audit.

Revision ID: 0001
Revises:
Create Date: 2026-11-25

Migrations intentionally do not import application enums/models so they remain valid as the
application code evolves. Constraint names follow the naming convention in database/base.py
(alembic applies target_metadata's convention to op.create_table).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op
from geoalchemy2 import Geography
from sqlalchemy.dialects import postgresql as pg

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _enum(name: str, *values: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True, length=32)


def _uuid_pk() -> sa.Column:
    return sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()"))


def _ts(name: str, nullable: bool = False, default_now: bool = True) -> sa.Column:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=nullable,
        server_default=sa.func.now() if default_now else None,
    )


def _timestamps() -> list[sa.Column]:
    return [_ts("created_at"), _ts("updated_at")]


def _maybe_enable_timescaledb() -> None:
    """TimescaleDB is optional (ADR-004): enable only if installed and preloaded."""
    if context.is_offline_mode():
        op.execute("-- TimescaleDB: enabled at runtime only if installed and preloaded")
        return
    bind = op.get_bind()
    available = bind.execute(
        sa.text("SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb'")
    ).scalar()
    preloaded = bind.execute(sa.text("SELECT current_setting('shared_preload_libraries')")).scalar()
    if available and preloaded and "timescaledb" in preloaded:
        op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    _maybe_enable_timescaledb()

    # --- identity -------------------------------------------------------------------------
    op.create_table(
        "organizations",
        _uuid_pk(),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False, unique=True),
        *_timestamps(),
    )
    op.create_table(
        "users",
        _uuid_pk(),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("full_name", sa.String(200)),
        sa.Column("password_hash", sa.Text()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_superuser", sa.Boolean(), nullable=False, server_default=sa.false()),
        _ts("last_login_at", nullable=True, default_now=False),
        *_timestamps(),
    )
    op.create_index("uq_users_email_lower", "users", [sa.text("lower(email)")], unique=True)

    op.create_table(
        "organization_members",
        _uuid_pk(),
        sa.Column(
            "organization_id",
            sa.Uuid(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "role", _enum("member_role", "owner", "admin", "analyst", "viewer"), nullable=False
        ),
        _ts("created_at"),
        sa.UniqueConstraint("organization_id", "user_id"),
    )
    op.create_index(op.f("ix_organization_members_user_id"), "organization_members", ["user_id"])

    op.create_table(
        "api_keys",
        _uuid_pk(),
        sa.Column(
            "organization_id",
            sa.Uuid(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_by_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("key_prefix", sa.String(16), nullable=False, unique=True),
        sa.Column("key_hash", sa.String(128), nullable=False),
        sa.Column("scopes", pg.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        _ts("last_used_at", nullable=True, default_now=False),
        _ts("expires_at", nullable=True, default_now=False),
        _ts("revoked_at", nullable=True, default_now=False),
        _ts("created_at"),
    )
    op.create_index(op.f("ix_api_keys_organization_id"), "api_keys", ["organization_id"])

    # --- data provenance -------------------------------------------------------------------
    op.create_table(
        "data_sources",
        _uuid_pk(),
        sa.Column("key", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column(
            "category",
            _enum(
                "data_source_category",
                "ais",
                "weather",
                "port",
                "carrier",
                "trade",
                "market",
                "internal",
            ),
            nullable=False,
        ),
        sa.Column(
            "data_mode",
            _enum("data_mode", "real_time", "periodic", "historical", "synthetic"),
            nullable=False,
        ),
        sa.Column(
            "status",
            _enum(
                "data_source_status", "active", "degraded", "error", "not_configured", "disabled"
            ),
            nullable=False,
            server_default="not_configured",
        ),
        sa.Column("homepage_url", sa.Text()),
        sa.Column("license_note", sa.Text()),
        sa.Column("attribution", sa.Text()),
        sa.Column("expected_update_interval_seconds", sa.Integer()),
        _ts("last_success_at", nullable=True, default_now=False),
        _ts("last_error_at", nullable=True, default_now=False),
        sa.Column("last_error_message", sa.Text()),
        *_timestamps(),
    )
    op.create_table(
        "ingestion_logs",
        _uuid_pk(),
        sa.Column(
            "data_source_id",
            sa.Uuid(),
            sa.ForeignKey("data_sources.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        _ts("started_at"),
        _ts("finished_at", nullable=True, default_now=False),
        sa.Column(
            "status",
            _enum("ingestion_status", "running", "succeeded", "partial", "failed"),
            nullable=False,
        ),
        sa.Column("records_received", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("records_accepted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("records_rejected", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("records_duplicate", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text()),
        sa.Column("details", pg.JSONB(), nullable=False, server_default="{}"),
    )
    op.create_index(
        "ix_ingestion_logs_source_started",
        "ingestion_logs",
        ["data_source_id", sa.text("started_at DESC")],
    )

    # --- carriers --------------------------------------------------------------------------
    op.create_table(
        "carriers",
        _uuid_pk(),
        sa.Column("code", sa.String(32), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("scac", sa.String(4), unique=True),
        sa.Column(
            "carrier_type",
            _enum("carrier_type", "ocean_carrier", "logistics_provider"),
            nullable=False,
        ),
        sa.Column("website_url", sa.Text()),
        *_timestamps(),
    )
    op.create_table(
        "carrier_integrations",
        _uuid_pk(),
        sa.Column(
            "carrier_id",
            sa.Uuid(),
            sa.ForeignKey("carriers.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "organization_id", sa.Uuid(), sa.ForeignKey("organizations.id", ondelete="CASCADE")
        ),
        sa.Column(
            "capability",
            _enum("integration_capability", "shipment_tracking", "vessel_schedules", "fleet_list"),
            nullable=False,
        ),
        sa.Column(
            "status",
            _enum(
                "integration_status",
                "connected",
                "authentication_required",
                "pending_approval",
                "unavailable",
                "demo_data_only",
            ),
            nullable=False,
        ),
        sa.Column("adapter_key", sa.String(64)),
        sa.Column("status_reason", sa.Text()),
        sa.Column("config", pg.JSONB(), nullable=False, server_default="{}"),
        _ts("last_checked_at", nullable=True, default_now=False),
        _ts("last_success_at", nullable=True, default_now=False),
        *_timestamps(),
        sa.UniqueConstraint(
            "carrier_id", "organization_id", "capability", postgresql_nulls_not_distinct=True
        ),
    )
    op.create_index(
        op.f("ix_carrier_integrations_organization_id"), "carrier_integrations", ["organization_id"]
    )
    op.create_table(
        "carrier_integration_logs",
        _uuid_pk(),
        sa.Column(
            "carrier_integration_id",
            sa.Uuid(),
            sa.ForeignKey("carrier_integrations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        _ts("occurred_at"),
        sa.Column("operation", sa.String(64), nullable=False),
        sa.Column(
            "outcome",
            _enum(
                "integration_outcome", "success", "failure", "rate_limited", "auth_error", "timeout"
            ),
            nullable=False,
        ),
        sa.Column("http_status", sa.SmallInteger()),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("error_code", sa.String(64)),
        sa.Column("message", sa.Text()),
        sa.Column("provider_request_id", sa.String(200)),
    )
    op.create_index(
        "ix_carrier_integration_logs_integration_occurred",
        "carrier_integration_logs",
        ["carrier_integration_id", sa.text("occurred_at DESC")],
    )

    # --- geography -------------------------------------------------------------------------
    op.create_table(
        "ports",
        _uuid_pk(),
        sa.Column("unlocode", sa.String(5), unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("country_code", sa.String(2), nullable=False),
        sa.Column(
            "location",
            Geography(geometry_type="POINT", srid=4326, spatial_index=False),
            nullable=False,
        ),
        sa.Column("timezone", sa.String(64)),
        sa.Column("source_id", sa.Uuid(), sa.ForeignKey("data_sources.id", ondelete="RESTRICT")),
        sa.Column("source_ref", sa.String(200)),
        *_timestamps(),
        sa.CheckConstraint("char_length(country_code) = 2", name="country_code_len"),
    )
    op.create_index("ix_ports_location", "ports", ["location"], postgresql_using="gist")
    op.create_index(op.f("ix_ports_country_code"), "ports", ["country_code"])

    # --- operations ------------------------------------------------------------------------
    op.create_table(
        "system_alerts",
        _uuid_pk(),
        sa.Column(
            "organization_id", sa.Uuid(), sa.ForeignKey("organizations.id", ondelete="CASCADE")
        ),
        sa.Column(
            "severity", _enum("alert_severity", "info", "warning", "critical"), nullable=False
        ),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("message", sa.Text()),
        sa.Column("entity_type", sa.String(64)),
        sa.Column("entity_id", sa.Uuid()),
        sa.Column(
            "data_source_id", sa.Uuid(), sa.ForeignKey("data_sources.id", ondelete="SET NULL")
        ),
        sa.Column("dedupe_key", sa.String(300)),
        _ts("raised_at"),
        _ts("acknowledged_at", nullable=True, default_now=False),
        sa.Column("acknowledged_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        _ts("resolved_at", nullable=True, default_now=False),
    )
    op.create_index(op.f("ix_system_alerts_organization_id"), "system_alerts", ["organization_id"])
    op.create_index("ix_system_alerts_raised_at", "system_alerts", [sa.text("raised_at DESC")])
    op.create_index(
        "uq_system_alerts_open_dedupe_key",
        "system_alerts",
        ["dedupe_key"],
        unique=True,
        postgresql_where=sa.text("resolved_at IS NULL"),
    )

    op.create_table(
        "audit_logs",
        _uuid_pk(),
        sa.Column(
            "organization_id", sa.Uuid(), sa.ForeignKey("organizations.id", ondelete="SET NULL")
        ),
        sa.Column("actor_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("actor_api_key_id", sa.Uuid(), sa.ForeignKey("api_keys.id", ondelete="SET NULL")),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("entity_type", sa.String(64)),
        sa.Column("entity_id", sa.Uuid()),
        _ts("occurred_at"),
        sa.Column("ip_address", pg.INET()),
        sa.Column("user_agent", sa.Text()),
        sa.Column("changes", pg.JSONB(), nullable=False, server_default="{}"),
    )
    op.create_index(
        "ix_audit_logs_org_occurred", "audit_logs", ["organization_id", sa.text("occurred_at DESC")]
    )


def downgrade() -> None:
    # Extensions are left installed: they may be shared with other schemas.
    for table in (
        "audit_logs",
        "system_alerts",
        "ports",
        "carrier_integration_logs",
        "carrier_integrations",
        "carriers",
        "ingestion_logs",
        "data_sources",
        "api_keys",
        "organization_members",
        "users",
        "organizations",
    ):
        op.drop_table(table)
