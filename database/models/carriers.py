from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, str_enum
from database.enums import (
    CarrierType,
    IntegrationCapability,
    IntegrationOutcome,
    IntegrationStatus,
)


class Carrier(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "carriers"

    code: Mapped[str] = mapped_column(sa.String(32), unique=True)
    name: Mapped[str] = mapped_column(sa.String(200))
    scac: Mapped[str | None] = mapped_column(sa.String(4), unique=True)
    carrier_type: Mapped[CarrierType] = mapped_column(str_enum(CarrierType, "carrier_type"))
    website_url: Mapped[str | None] = mapped_column(sa.Text)

    integrations: Mapped[list[CarrierIntegration]] = relationship(
        back_populates="carrier", lazy="raise"
    )


class CarrierIntegration(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """State of *our* connection to a carrier for one capability.

    `organization_id` NULL means the platform-level default integration.
    Secrets are never stored here; `config` holds non-secret settings only.
    """

    __tablename__ = "carrier_integrations"
    __table_args__ = (
        sa.UniqueConstraint(
            "carrier_id",
            "organization_id",
            "capability",
            postgresql_nulls_not_distinct=True,
        ),
    )

    carrier_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("carriers.id", ondelete="RESTRICT"))
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    capability: Mapped[IntegrationCapability] = mapped_column(
        str_enum(IntegrationCapability, "integration_capability")
    )
    status: Mapped[IntegrationStatus] = mapped_column(
        str_enum(IntegrationStatus, "integration_status")
    )
    adapter_key: Mapped[str | None] = mapped_column(sa.String(64))
    status_reason: Mapped[str | None] = mapped_column(sa.Text)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    last_checked_at: Mapped[datetime | None]
    last_success_at: Mapped[datetime | None]

    carrier: Mapped[Carrier] = relationship(back_populates="integrations", lazy="raise")


class CarrierIntegrationLog(UUIDPrimaryKeyMixin, Base):
    """Per-call integration log.

    Never stores request/response bodies: they may contain customer data.
    """

    __tablename__ = "carrier_integration_logs"
    __table_args__ = (
        sa.Index(
            "ix_carrier_integration_logs_integration_occurred",
            "carrier_integration_id",
            sa.text("occurred_at DESC"),
        ),
    )

    carrier_integration_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("carrier_integrations.id", ondelete="CASCADE")
    )
    occurred_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())
    operation: Mapped[str] = mapped_column(sa.String(64))
    outcome: Mapped[IntegrationOutcome] = mapped_column(
        str_enum(IntegrationOutcome, "integration_outcome")
    )
    http_status: Mapped[int | None] = mapped_column(sa.SmallInteger)
    latency_ms: Mapped[int | None]
    error_code: Mapped[str | None] = mapped_column(sa.String(64))
    message: Mapped[str | None] = mapped_column(sa.Text)
    provider_request_id: Mapped[str | None] = mapped_column(sa.String(200))
