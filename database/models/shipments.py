"""Shipment intelligence: shipments, containers, vessel legs, and append-only events."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, str_enum
from database.enums import (
    DelayStatus,
    EventClassifier,
    ReferenceType,
    ShipmentEventType,
    ShipmentStatus,
)


class Shipment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A tracked reference (container / booking / B/L) with carrier-of-record.

    `planned_arrival_at` is the FIRST planned arrival at the destination ever observed and is
    write-once: later re-plans never overwrite it, so delay is measured against the original
    plan. Status, ETA and delay fields are derived from `shipment_events`."""

    __tablename__ = "shipments"
    __table_args__ = (
        sa.UniqueConstraint("organization_id", "carrier_id", "reference_type", "reference_value"),
        sa.Index("ix_shipments_org_updated", "organization_id", sa.text("updated_at DESC")),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("organizations.id", ondelete="CASCADE")
    )
    carrier_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("carriers.id", ondelete="RESTRICT"), index=True
    )
    reference_type: Mapped[ReferenceType] = mapped_column(str_enum(ReferenceType, "reference_type"))
    reference_value: Mapped[str] = mapped_column(sa.String(64))
    origin_unlocode: Mapped[str | None] = mapped_column(sa.String(5))
    destination_unlocode: Mapped[str | None] = mapped_column(sa.String(5))
    origin_port_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("ports.id", ondelete="SET NULL")
    )
    destination_port_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("ports.id", ondelete="SET NULL")
    )
    status: Mapped[ShipmentStatus] = mapped_column(
        str_enum(ShipmentStatus, "shipment_status"), server_default=ShipmentStatus.UNKNOWN.value
    )
    planned_arrival_at: Mapped[datetime | None]
    current_eta_at: Mapped[datetime | None]
    actual_arrival_at: Mapped[datetime | None]
    delay_hours: Mapped[Decimal | None] = mapped_column(sa.Numeric(8, 2))
    delay_status: Mapped[DelayStatus] = mapped_column(
        str_enum(DelayStatus, "delay_status"), server_default=DelayStatus.UNKNOWN.value
    )
    last_event_at: Mapped[datetime | None]
    last_refreshed_at: Mapped[datetime | None]
    source_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("data_sources.id", ondelete="RESTRICT")
    )


class Container(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "containers"
    __table_args__ = (sa.UniqueConstraint("organization_id", "container_number"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("organizations.id", ondelete="CASCADE")
    )
    container_number: Mapped[str] = mapped_column(sa.String(11))
    iso_size_type: Mapped[str | None] = mapped_column(sa.String(4))
    # ISO 6346 check digit result; invalid numbers are kept (carriers do send them) but flagged.
    check_digit_valid: Mapped[bool]
    created_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())


class ShipmentItem(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "shipment_items"
    __table_args__ = (sa.UniqueConstraint("shipment_id", "container_id"),)

    shipment_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("shipments.id", ondelete="CASCADE")
    )
    container_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("containers.id", ondelete="CASCADE"), index=True
    )
    description: Mapped[str | None] = mapped_column(sa.Text)
    weight_kg: Mapped[Decimal | None] = mapped_column(sa.Numeric(12, 2))
    hs_code: Mapped[str | None] = mapped_column(sa.String(10))


class ShipmentVessel(UUIDPrimaryKeyMixin, Base):
    """A vessel leg as reported by the carrier. `vessel_id` is set only when the carrier
    reports an IMO that matches a tracked vessel: never inferred from AIS proximity."""

    __tablename__ = "shipment_vessels"
    __table_args__ = (sa.UniqueConstraint("shipment_id", "leg_sequence"),)

    shipment_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("shipments.id", ondelete="CASCADE")
    )
    vessel_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("vessels.id", ondelete="SET NULL"), index=True
    )
    leg_sequence: Mapped[int] = mapped_column(sa.SmallInteger)
    carrier_vessel_name: Mapped[str | None] = mapped_column(sa.String(120))
    carrier_vessel_imo: Mapped[int | None]
    carrier_voyage_number: Mapped[str | None] = mapped_column(sa.String(32))
    load_unlocode: Mapped[str | None] = mapped_column(sa.String(5))
    discharge_unlocode: Mapped[str | None] = mapped_column(sa.String(5))
    source_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("data_sources.id", ondelete="RESTRICT")
    )
    updated_at: Mapped[datetime] = mapped_column(
        server_default=sa.func.now(), onupdate=sa.func.now()
    )


class ShipmentEvent(UUIDPrimaryKeyMixin, Base):
    """Append-only milestone events (DCSA Track & Trace model). A new estimate is a new row."""

    __tablename__ = "shipment_events"
    __table_args__ = (
        sa.UniqueConstraint("shipment_id", "dedupe_key"),
        sa.Index("ix_shipment_events_shipment_time", "shipment_id", "event_at"),
    )

    shipment_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("shipments.id", ondelete="CASCADE")
    )
    container_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("containers.id", ondelete="SET NULL")
    )
    event_type: Mapped[ShipmentEventType] = mapped_column(
        str_enum(ShipmentEventType, "shipment_event_type")
    )
    event_code: Mapped[str] = mapped_column(sa.String(16))
    classifier: Mapped[EventClassifier] = mapped_column(
        str_enum(EventClassifier, "event_classifier")
    )
    event_at: Mapped[datetime]
    event_created_at: Mapped[datetime | None]  # when the provider created/issued the event
    location_unlocode: Mapped[str | None] = mapped_column(sa.String(5))
    location_name: Mapped[str | None] = mapped_column(sa.String(200))
    vessel_imo: Mapped[int | None]
    vessel_name: Mapped[str | None] = mapped_column(sa.String(120))
    voyage_number: Mapped[str | None] = mapped_column(sa.String(32))
    transport_mode: Mapped[str | None] = mapped_column(sa.String(16))
    description: Mapped[str | None] = mapped_column(sa.Text)
    raw_code: Mapped[str | None] = mapped_column(sa.String(64))  # provider's original code
    source_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("data_sources.id", ondelete="RESTRICT")
    )
    source_event_id: Mapped[str | None] = mapped_column(sa.String(128))
    # Provider event id when present, otherwise a hash of the event's identifying fields.
    dedupe_key: Mapped[str] = mapped_column(sa.String(128))
    ingested_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())
