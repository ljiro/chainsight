"""Port geofences, routes, activity observations, and weather."""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from geoalchemy2 import Geography
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base, UUIDPrimaryKeyMixin, str_enum
from database.enums import WeatherKind


class PortArea(UUIDPrimaryKeyMixin, Base):
    """Geofence used for AIS-based arrival/departure detection."""

    __tablename__ = "port_areas"
    __table_args__ = (sa.Index("ix_port_areas_area", "area", postgresql_using="gist"),)

    port_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("ports.id", ondelete="CASCADE"), index=True
    )
    area: Mapped[object] = mapped_column(
        Geography(geometry_type="POLYGON", srid=4326, spatial_index=False)
    )
    radius_m: Mapped[int | None]
    # 'circle_buffer' (radius around the registry point) | 'polygon' (curated outline)
    method: Mapped[str] = mapped_column(sa.String(32))
    created_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())


class MaritimeRoute(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "maritime_routes"

    name: Mapped[str] = mapped_column(sa.String(200))
    origin_port_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("ports.id", ondelete="RESTRICT")
    )
    destination_port_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("ports.id", ondelete="RESTRICT")
    )
    path: Mapped[object] = mapped_column(
        Geography(geometry_type="LINESTRING", srid=4326, spatial_index=False)
    )
    distance_nm: Mapped[float | None] = mapped_column(sa.REAL)
    source: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())


class PortObservation(Base):
    """Port activity indicators. Hypertable on `observed_at` when TimescaleDB is present.

    `available_at` is when the value could first have been known (publication lag matters
    for research; see docs/architecture/system-design.md §5.1)."""

    __tablename__ = "port_observations"

    port_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("ports.id", ondelete="CASCADE"), primary_key=True
    )
    metric: Mapped[str] = mapped_column(sa.String(64), primary_key=True)
    observed_at: Mapped[datetime] = mapped_column(primary_key=True)
    source_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("data_sources.id", ondelete="RESTRICT"), primary_key=True
    )
    available_at: Mapped[datetime]
    value: Mapped[float] = mapped_column(sa.Double)
    ingested_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())


class WeatherObservation(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "weather_observations"
    __table_args__ = (
        sa.UniqueConstraint("port_id", "kind", "valid_at", "source_id"),
        sa.Index("ix_weather_observations_port_valid", "port_id", sa.text("valid_at DESC")),
    )

    port_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("ports.id", ondelete="CASCADE"))
    location: Mapped[object] = mapped_column(
        Geography(geometry_type="POINT", srid=4326, spatial_index=False)
    )
    kind: Mapped[WeatherKind] = mapped_column(str_enum(WeatherKind, "weather_kind"))
    valid_at: Mapped[datetime]
    retrieved_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())
    temperature_c: Mapped[float | None] = mapped_column(sa.REAL)
    wind_speed_kn: Mapped[float | None] = mapped_column(sa.REAL)
    wind_direction_deg: Mapped[float | None] = mapped_column(sa.REAL)
    wind_gusts_kn: Mapped[float | None] = mapped_column(sa.REAL)
    precipitation_mm: Mapped[float | None] = mapped_column(sa.REAL)
    source_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("data_sources.id", ondelete="RESTRICT")
    )
