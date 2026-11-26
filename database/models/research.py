"""Quantitative research: external indicators (point-in-time), derived maritime features,
experiments and backtests.

Point-in-time rule: every value carries `available_at`, the first moment it could have been
known. Research code reads with `available_at <= decision_time`. A changed value is never
overwritten: indicator revisions and feature versions are new rows with a later
`available_at`, so history can be replayed exactly as it was known.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base, UUIDPrimaryKeyMixin


class IndicatorSeries(UUIDPrimaryKeyMixin, Base):
    """One external time series, e.g. PortWatch tanker port calls at Rotterdam or FRED Brent."""

    __tablename__ = "indicator_series"
    __table_args__ = (
        sa.CheckConstraint("category IN ('maritime', 'market')", name="indicator_category"),
    )

    key: Mapped[str] = mapped_column(sa.String(200), unique=True)
    source_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("data_sources.id", ondelete="RESTRICT"), index=True
    )
    category: Mapped[str] = mapped_column(sa.String(16))
    name: Mapped[str] = mapped_column(sa.String(300))
    entity_kind: Mapped[str] = mapped_column(sa.String(32))  # port | chokepoint | asset
    entity_key: Mapped[str] = mapped_column(sa.String(100))
    entity_name: Mapped[str] = mapped_column(sa.String(200))
    metric: Mapped[str] = mapped_column(sa.String(100))
    unit: Mapped[str | None] = mapped_column(sa.String(50))
    frequency: Mapped[str] = mapped_column(sa.String(16), server_default="daily")
    # Assumed lag between the period and its publication, used for history loaded in bulk
    # (when the true first-publication time is unknown). Live ingestion uses the real time.
    publication_lag_days: Mapped[int] = mapped_column(sa.SmallInteger)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())


class IndicatorObservation(Base):
    """Hypertable on `as_of`. A revised value is a new row with revision + 1."""

    __tablename__ = "indicator_observations"

    series_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("indicator_series.id", ondelete="CASCADE"), primary_key=True
    )
    as_of: Mapped[datetime] = mapped_column(primary_key=True)  # period (day) start, UTC
    revision: Mapped[int] = mapped_column(sa.SmallInteger, primary_key=True, server_default="0")
    value: Mapped[float] = mapped_column(sa.Double)
    available_at: Mapped[datetime]
    # True when `available_at` is derived from the assumed lag (bulk history), false when it
    # is the actual time ChainSight first saw the value.
    available_at_assumed: Mapped[bool] = mapped_column(server_default=sa.false())
    ingested_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())


class ResearchDataset(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "research_datasets"

    key: Mapped[str] = mapped_column(sa.String(100), unique=True)
    name: Mapped[str] = mapped_column(sa.String(200))
    description: Mapped[str | None] = mapped_column(sa.Text)
    frequency: Mapped[str] = mapped_column(sa.String(16))
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB)  # the reproducible recipe
    content_hash: Mapped[str | None] = mapped_column(sa.String(64))
    created_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())


class MaritimeFeature(Base):
    """Derived features. Hypertable on `as_of` (period end). A recomputation that changes a
    value adds a new version; earlier versions stay for point-in-time replay."""

    __tablename__ = "maritime_features"

    feature_key: Mapped[str] = mapped_column(sa.String(200), primary_key=True)
    as_of: Mapped[datetime] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(sa.SmallInteger, primary_key=True, server_default="1")
    value: Mapped[float] = mapped_column(sa.Double)
    available_at: Mapped[datetime]
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("research_datasets.id", ondelete="CASCADE"), index=True
    )
    computed_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())


class ResearchExperiment(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "research_experiments"
    __table_args__ = (
        sa.CheckConstraint(
            "status IN ('draft', 'completed', 'failed')", name="research_experiment_status"
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(sa.String(200))
    hypothesis: Mapped[str] = mapped_column(sa.Text)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(sa.String(16), server_default="draft")
    created_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())


class BacktestRun(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "backtest_runs"
    __table_args__ = (
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')", name="backtest_run_status"
        ),
        sa.Index(
            "ix_backtest_runs_experiment_started", "experiment_id", sa.text("started_at DESC")
        ),
    )

    experiment_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("research_experiments.id", ondelete="CASCADE")
    )
    started_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())
    finished_at: Mapped[datetime | None]
    status: Mapped[str] = mapped_column(sa.String(16), server_default="running")
    error: Mapped[str | None] = mapped_column(sa.Text)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB)
    code_version: Mapped[str | None] = mapped_column(sa.String(64))
    data_snapshot_hash: Mapped[str | None] = mapped_column(sa.String(64))
    # Daily equity / benchmark / position / drawdown, and per-fold parameter choices.
    series: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    folds: Mapped[list[Any]] = mapped_column(JSONB, server_default="[]")


class BacktestMetric(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "backtest_metrics"
    __table_args__ = (
        sa.UniqueConstraint("backtest_run_id", "split", "metric"),
        sa.CheckConstraint("split IN ('in_sample', 'out_of_sample')", name="backtest_split"),
    )

    backtest_run_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("backtest_runs.id", ondelete="CASCADE")
    )
    split: Mapped[str] = mapped_column(sa.String(16))
    metric: Mapped[str] = mapped_column(sa.String(64))
    value: Mapped[float | None] = mapped_column(sa.Double)
    benchmark_value: Mapped[float | None] = mapped_column(sa.Double)


class ResearchSignal(UUIDPrimaryKeyMixin, Base):
    """The signal and position a backtest actually used, per decision date."""

    __tablename__ = "research_signals"
    __table_args__ = (sa.Index("ix_research_signals_run_as_of", "backtest_run_id", "as_of"),)

    experiment_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("research_experiments.id", ondelete="CASCADE"), index=True
    )
    backtest_run_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("backtest_runs.id", ondelete="CASCADE")
    )
    signal_key: Mapped[str] = mapped_column(sa.String(200))
    as_of: Mapped[datetime]
    available_at: Mapped[datetime]
    value: Mapped[float | None] = mapped_column(sa.Double)
    position: Mapped[float | None] = mapped_column(sa.Double)
