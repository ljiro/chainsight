"""Fleet intelligence: vessels, identity history, positions, voyages, port calls."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa
from geoalchemy2 import Geography
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, str_enum
from database.enums import (
    AssociationMethod,
    CarrierRelation,
    PortCallDetection,
    VesselClass,
    VesselIdentifierScheme,
    VoyageStatus,
)


def _point() -> Geography:
    return Geography(geometry_type="POINT", srid=4326, spatial_index=False)


class Vessel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Resolved vessel identity plus a denormalized copy of the latest position (for the map)."""

    __tablename__ = "vessels"
    __table_args__ = (
        sa.Index("ix_vessels_last_location", "last_location", postgresql_using="gist"),
        sa.Index("ix_vessels_last_seen_at", sa.text("last_seen_at DESC")),
    )

    mmsi: Mapped[int | None] = mapped_column(index=True)
    imo: Mapped[int | None] = mapped_column(unique=True)
    name: Mapped[str | None] = mapped_column(sa.String(120))
    call_sign: Mapped[str | None] = mapped_column(sa.String(16))
    ship_type_code: Mapped[int | None] = mapped_column(sa.SmallInteger)
    vessel_class: Mapped[VesselClass] = mapped_column(
        str_enum(VesselClass, "vessel_class"), server_default=VesselClass.UNKNOWN.value
    )
    # Where vessel_class came from: 'ais_ship_type' | 'registry' | 'manual' | NULL (unknown).
    vessel_class_source: Mapped[str | None] = mapped_column(sa.String(32))
    length_m: Mapped[int | None] = mapped_column(sa.SmallInteger)
    beam_m: Mapped[int | None] = mapped_column(sa.SmallInteger)
    draught_m: Mapped[float | None] = mapped_column(sa.REAL)
    destination: Mapped[str | None] = mapped_column(sa.String(64))  # as reported in AIS
    first_seen_at: Mapped[datetime | None]
    last_seen_at: Mapped[datetime | None]
    last_location: Mapped[object | None] = mapped_column(_point())
    last_sog_knots: Mapped[float | None] = mapped_column(sa.REAL)
    last_cog_deg: Mapped[float | None] = mapped_column(sa.REAL)
    last_heading_deg: Mapped[int | None] = mapped_column(sa.SmallInteger)
    last_nav_status: Mapped[int | None] = mapped_column(sa.SmallInteger)
    last_position_source_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("data_sources.id", ondelete="SET NULL")
    )


class VesselIdentifier(UUIDPrimaryKeyMixin, Base):
    """Identifier history. MMSI is reassigned on reflagging, so identity is time-bounded."""

    __tablename__ = "vessel_identifiers"
    __table_args__ = (
        sa.Index(
            "uq_vessel_identifiers_active_scheme_value",
            "scheme",
            "value",
            unique=True,
            postgresql_where=sa.text("valid_to IS NULL AND scheme IN ('mmsi', 'imo')"),
        ),
    )

    vessel_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("vessels.id", ondelete="CASCADE"), index=True
    )
    scheme: Mapped[VesselIdentifierScheme] = mapped_column(
        str_enum(VesselIdentifierScheme, "vessel_identifier_scheme")
    )
    value: Mapped[str] = mapped_column(sa.String(120))
    valid_from: Mapped[datetime] = mapped_column(server_default=sa.func.now())
    valid_to: Mapped[datetime | None]
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("data_sources.id", ondelete="SET NULL")
    )


class VesselCarrierHistory(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "vessel_carrier_history"

    vessel_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("vessels.id", ondelete="CASCADE"), index=True
    )
    carrier_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("carriers.id", ondelete="RESTRICT"), index=True
    )
    relation: Mapped[CarrierRelation] = mapped_column(str_enum(CarrierRelation, "carrier_relation"))
    method: Mapped[AssociationMethod] = mapped_column(
        str_enum(AssociationMethod, "association_method")
    )
    confidence: Mapped[Decimal] = mapped_column(sa.Numeric(3, 2))
    valid_from: Mapped[datetime] = mapped_column(server_default=sa.func.now())
    valid_to: Mapped[datetime | None]
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("data_sources.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())


class VesselPosition(Base):
    """AIS positions. TimescaleDB hypertable on `observed_at` when the extension is present.

    The primary key is also the dedup key (same vessel, same instant, same source)."""

    __tablename__ = "vessel_positions"

    vessel_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("vessels.id", ondelete="CASCADE"), primary_key=True
    )
    observed_at: Mapped[datetime] = mapped_column(primary_key=True)
    source_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("data_sources.id", ondelete="RESTRICT"), primary_key=True
    )
    ingested_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())
    location: Mapped[object] = mapped_column(_point())
    sog_knots: Mapped[float | None] = mapped_column(sa.REAL)
    cog_deg: Mapped[float | None] = mapped_column(sa.REAL)
    heading_deg: Mapped[int | None] = mapped_column(sa.SmallInteger)
    nav_status: Mapped[int | None] = mapped_column(sa.SmallInteger)
    rate_of_turn: Mapped[float | None] = mapped_column(sa.REAL)
    position_accuracy_high: Mapped[bool | None]
    message_type: Mapped[str] = mapped_column(sa.String(48))
    quality_flags: Mapped[int] = mapped_column(sa.SmallInteger, server_default="0")


class VesselPositionSource(UUIDPrimaryKeyMixin, Base):
    """Per-window statistics about where a batch of positions came from."""

    __tablename__ = "vessel_position_sources"

    ingestion_log_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("ingestion_logs.id", ondelete="CASCADE"), index=True
    )
    data_source_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("data_sources.id", ondelete="RESTRICT")
    )
    # 'terrestrial' | 'satellite' | 'synthetic' | 'unknown'
    receiver_type: Mapped[str] = mapped_column(sa.String(16))
    window_start: Mapped[datetime]
    window_end: Mapped[datetime]
    messages: Mapped[int]
    positions: Mapped[int]
    mean_latency_ms: Mapped[float | None] = mapped_column(sa.REAL)


class Voyage(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "voyages"
    __table_args__ = (
        sa.Index("ix_voyages_vessel_departed", "vessel_id", sa.text("departed_at DESC")),
    )

    vessel_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("vessels.id", ondelete="CASCADE"))
    carrier_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("carriers.id", ondelete="SET NULL")
    )
    voyage_number: Mapped[str | None] = mapped_column(sa.String(32))
    service_code: Mapped[str | None] = mapped_column(sa.String(32))
    origin_port_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("ports.id", ondelete="RESTRICT")
    )
    destination_port_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("ports.id", ondelete="RESTRICT")
    )
    departed_at: Mapped[datetime | None]
    arrived_at: Mapped[datetime | None]
    status: Mapped[VoyageStatus] = mapped_column(str_enum(VoyageStatus, "voyage_status"))
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("data_sources.id", ondelete="SET NULL")
    )


class VoyagePortCall(UUIDPrimaryKeyMixin, Base):
    """Planned / estimated / actual times live side by side; none overwrites another."""

    __tablename__ = "voyage_port_calls"
    __table_args__ = (
        sa.Index(
            "ix_voyage_port_calls_vessel_arrival", "vessel_id", sa.text("actual_arrival_at DESC")
        ),
        sa.Index("ix_voyage_port_calls_port_arrival", "port_id", sa.text("actual_arrival_at DESC")),
    )

    vessel_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("vessels.id", ondelete="CASCADE"))
    voyage_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("voyages.id", ondelete="SET NULL")
    )
    port_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("ports.id", ondelete="RESTRICT"))
    sequence: Mapped[int | None] = mapped_column(sa.SmallInteger)
    planned_arrival_at: Mapped[datetime | None]
    estimated_arrival_at: Mapped[datetime | None]
    actual_arrival_at: Mapped[datetime | None]
    planned_departure_at: Mapped[datetime | None]
    estimated_departure_at: Mapped[datetime | None]
    actual_departure_at: Mapped[datetime | None]
    detection_method: Mapped[PortCallDetection] = mapped_column(
        str_enum(PortCallDetection, "port_call_detection")
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("data_sources.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())
