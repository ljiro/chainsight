from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base, UUIDPrimaryKeyMixin, str_enum
from database.enums import AlertSeverity


class SystemAlert(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "system_alerts"
    __table_args__ = (
        # At most one *open* alert per dedupe key.
        sa.Index(
            "uq_system_alerts_open_dedupe_key",
            "dedupe_key",
            unique=True,
            postgresql_where=sa.text("resolved_at IS NULL"),
        ),
        sa.Index("ix_system_alerts_raised_at", sa.text("raised_at DESC")),
    )

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    severity: Mapped[AlertSeverity] = mapped_column(str_enum(AlertSeverity, "alert_severity"))
    category: Mapped[str] = mapped_column(sa.String(64))
    title: Mapped[str] = mapped_column(sa.String(300))
    message: Mapped[str | None] = mapped_column(sa.Text)
    entity_type: Mapped[str | None] = mapped_column(sa.String(64))
    entity_id: Mapped[uuid.UUID | None]
    data_source_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("data_sources.id", ondelete="SET NULL")
    )
    dedupe_key: Mapped[str | None] = mapped_column(sa.String(300))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    raised_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())
    acknowledged_at: Mapped[datetime | None]
    acknowledged_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    resolved_at: Mapped[datetime | None]


class AuditLog(UUIDPrimaryKeyMixin, Base):
    """Append-only audit trail."""

    __tablename__ = "audit_logs"
    __table_args__ = (
        sa.Index("ix_audit_logs_org_occurred", "organization_id", sa.text("occurred_at DESC")),
    )

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("organizations.id", ondelete="SET NULL")
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    actor_api_key_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("api_keys.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(sa.String(100))
    entity_type: Mapped[str | None] = mapped_column(sa.String(64))
    entity_id: Mapped[uuid.UUID | None]
    occurred_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())
    ip_address: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(sa.Text)
    changes: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
