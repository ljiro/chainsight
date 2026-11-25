from __future__ import annotations

import uuid

import sqlalchemy as sa
from geoalchemy2 import Geography
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Port(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "ports"
    __table_args__ = (
        sa.Index("ix_ports_location", "location", postgresql_using="gist"),
        sa.CheckConstraint("char_length(country_code) = 2", name="country_code_len"),
    )

    unlocode: Mapped[str | None] = mapped_column(sa.String(5), unique=True)
    name: Mapped[str] = mapped_column(sa.String(200))
    country_code: Mapped[str] = mapped_column(sa.String(2), index=True)
    # Spatial index declared explicitly above so migrations and models stay in sync.
    location: Mapped[object] = mapped_column(
        Geography(geometry_type="POINT", srid=4326, spatial_index=False)
    )
    timezone: Mapped[str | None] = mapped_column(sa.String(64))
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("data_sources.id", ondelete="RESTRICT")
    )
    source_ref: Mapped[str | None] = mapped_column(sa.String(200))
