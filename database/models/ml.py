"""Model registry, evaluations and append-only predictions.

Predictions are never overwritten: a database trigger (migration 0005) rejects UPDATEs that
change any predicted value, timestamp or model reference. Only outcome columns (actual
values, errors, resolved_at) may be filled in later.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base, UUIDPrimaryKeyMixin


class ModelVersion(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "model_versions"
    __table_args__ = (
        sa.UniqueConstraint("model_name", "version"),
        sa.CheckConstraint(
            "status IN ('candidate', 'production', 'archived')", name="model_status"
        ),
    )

    model_name: Mapped[str] = mapped_column(sa.String(64))  # eta | delay | congestion
    version: Mapped[str] = mapped_column(sa.String(64))
    algorithm: Mapped[str] = mapped_column(sa.String(64))
    status: Mapped[str] = mapped_column(sa.String(16), server_default="candidate")
    mlflow_run_id: Mapped[str | None] = mapped_column(sa.String(64))
    dataset_hash: Mapped[str | None] = mapped_column(sa.String(64))
    training_window_start: Mapped[datetime | None]
    training_window_end: Mapped[datetime | None]
    feature_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    hyperparameters: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    n_train: Mapped[int | None]
    n_test: Mapped[int | None]
    # Models fitted on synthetic data are flagged everywhere they appear.
    is_synthetic_trained: Mapped[bool]
    artifact_uri: Mapped[str | None] = mapped_column(sa.Text)
    notes: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())


class ModelEvaluation(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "model_evaluations"
    __table_args__ = (
        sa.CheckConstraint("split IN ('validation', 'test', 'live')", name="evaluation_split"),
        sa.Index(
            "ix_model_evaluations_model_evaluated", "model_version_id", sa.text("evaluated_at DESC")
        ),
    )

    model_version_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("model_versions.id", ondelete="CASCADE")
    )
    evaluated_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())
    split: Mapped[str] = mapped_column(sa.String(16))
    window_start: Mapped[datetime | None]
    window_end: Mapped[datetime | None]
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB)
    n_samples: Mapped[int]
    data_mode: Mapped[str] = mapped_column(sa.String(16))  # synthetic | real | mixed


class EtaPrediction(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "eta_predictions"
    __table_args__ = (
        sa.Index("ix_eta_predictions_vessel_predicted", "vessel_id", sa.text("predicted_at DESC")),
        sa.Index(
            "ix_eta_predictions_unresolved",
            "target_port_id",
            postgresql_where=sa.text("resolved_at IS NULL"),
        ),
    )

    vessel_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("vessels.id", ondelete="CASCADE"))
    target_port_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("ports.id", ondelete="SET NULL")
    )
    target_unlocode: Mapped[str | None] = mapped_column(sa.String(5))
    model_version_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("model_versions.id", ondelete="RESTRICT")
    )
    predicted_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())
    input_data_as_of: Mapped[datetime]
    predicted_arrival_at: Mapped[datetime]
    lower_bound_at: Mapped[datetime | None]
    upper_bound_at: Mapped[datetime | None]
    interval_level: Mapped[Decimal | None] = mapped_column(sa.Numeric(3, 2))
    remaining_distance_nm: Mapped[float | None] = mapped_column(sa.REAL)
    features: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    # Outcome (filled in once the arrival is observed).
    actual_arrival_at: Mapped[datetime | None]
    error_hours: Mapped[float | None] = mapped_column(sa.REAL)
    resolved_at: Mapped[datetime | None]


class DelayPrediction(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "delay_predictions"
    __table_args__ = (
        sa.Index(
            "ix_delay_predictions_shipment_predicted", "shipment_id", sa.text("predicted_at DESC")
        ),
    )

    shipment_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("shipments.id", ondelete="CASCADE")
    )
    model_version_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("model_versions.id", ondelete="RESTRICT")
    )
    predicted_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())
    input_data_as_of: Mapped[datetime]
    delay_probability: Mapped[float] = mapped_column(sa.REAL)
    threshold_hours: Mapped[float] = mapped_column(sa.REAL)
    calibration_method: Mapped[str | None] = mapped_column(sa.String(32))
    features: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    actual_delayed: Mapped[bool | None]
    actual_delay_hours: Mapped[float | None] = mapped_column(sa.REAL)
    resolved_at: Mapped[datetime | None]


class CongestionPrediction(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "congestion_predictions"
    __table_args__ = (sa.Index("ix_congestion_predictions_port_target", "port_id", "target_time"),)

    port_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("ports.id", ondelete="CASCADE"))
    metric: Mapped[str] = mapped_column(sa.String(64))
    target_time: Mapped[datetime]
    model_version_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("model_versions.id", ondelete="RESTRICT")
    )
    predicted_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())
    input_data_as_of: Mapped[datetime]
    predicted_value: Mapped[float] = mapped_column(sa.REAL)
    lower_bound: Mapped[float | None] = mapped_column(sa.REAL)
    upper_bound: Mapped[float | None] = mapped_column(sa.REAL)
    actual_value: Mapped[float | None] = mapped_column(sa.REAL)
    resolved_at: Mapped[datetime | None]


class RiskAssessment(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "risk_assessments"
    __table_args__ = (
        sa.CheckConstraint("level IN ('low', 'medium', 'high')", name="risk_level"),
        sa.Index(
            "ix_risk_assessments_shipment_assessed", "shipment_id", sa.text("assessed_at DESC")
        ),
    )

    shipment_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("shipments.id", ondelete="CASCADE")
    )
    vessel_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("vessels.id", ondelete="CASCADE")
    )
    model_version_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("model_versions.id", ondelete="RESTRICT")
    )
    assessed_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())
    score: Mapped[Decimal] = mapped_column(sa.Numeric(4, 3))
    level: Mapped[str] = mapped_column(sa.String(8))
    factors: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default="[]")
