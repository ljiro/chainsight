from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, str_enum
from database.enums import DataMode, DataSourceCategory, DataSourceStatus, IngestionStatus


class DataSource(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Registry of every external provider/feed. Referenced by ingested facts for provenance."""

    __tablename__ = "data_sources"

    key: Mapped[str] = mapped_column(sa.String(64), unique=True)
    name: Mapped[str] = mapped_column(sa.String(200))
    category: Mapped[DataSourceCategory] = mapped_column(
        str_enum(DataSourceCategory, "data_source_category")
    )
    data_mode: Mapped[DataMode] = mapped_column(str_enum(DataMode, "data_mode"))
    status: Mapped[DataSourceStatus] = mapped_column(
        str_enum(DataSourceStatus, "data_source_status"),
        server_default=DataSourceStatus.NOT_CONFIGURED.value,
    )
    homepage_url: Mapped[str | None] = mapped_column(sa.Text)
    license_note: Mapped[str | None] = mapped_column(sa.Text)
    attribution: Mapped[str | None] = mapped_column(sa.Text)
    expected_update_interval_seconds: Mapped[int | None]
    last_success_at: Mapped[datetime | None]
    last_error_at: Mapped[datetime | None]
    last_error_message: Mapped[str | None] = mapped_column(sa.Text)


class IngestionLog(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "ingestion_logs"
    __table_args__ = (
        sa.Index("ix_ingestion_logs_source_started", "data_source_id", sa.text("started_at DESC")),
    )

    data_source_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("data_sources.id", ondelete="RESTRICT")
    )
    started_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())
    finished_at: Mapped[datetime | None]
    status: Mapped[IngestionStatus] = mapped_column(str_enum(IngestionStatus, "ingestion_status"))
    records_received: Mapped[int] = mapped_column(server_default="0")
    records_accepted: Mapped[int] = mapped_column(server_default="0")
    records_rejected: Mapped[int] = mapped_column(server_default="0")
    records_duplicate: Mapped[int] = mapped_column(server_default="0")
    error_message: Mapped[str | None] = mapped_column(sa.Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
