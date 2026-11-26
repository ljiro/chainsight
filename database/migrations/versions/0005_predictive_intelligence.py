"""Predictive intelligence: model registry, evaluations, append-only predictions
(ETA, delay, congestion, risk) and system_alerts.details.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-26

A trigger enforces the append-only rule: once written, a prediction's predicted values,
timestamps and model reference cannot change. Only outcome columns may be filled later.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# table -> columns that may change after insert (outcomes); everything else is frozen.
MUTABLE_COLUMNS = {
    "eta_predictions": ("actual_arrival_at", "error_hours", "resolved_at"),
    "delay_predictions": ("actual_delayed", "actual_delay_hours", "resolved_at"),
    "congestion_predictions": ("actual_value", "resolved_at"),
}


def _uuid_pk() -> sa.Column:
    return sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()"))


def _ts(name: str, nullable: bool = True, default_now: bool = False) -> sa.Column:
    return sa.Column(
        name, sa.DateTime(timezone=True), nullable=nullable,
        server_default=sa.func.now() if default_now else None,
    )  # fmt: skip


def _fk(name: str, target: str, ondelete: str, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.Uuid(), sa.ForeignKey(target, ondelete=ondelete), nullable=nullable)


def upgrade() -> None:
    op.add_column(
        "system_alerts", sa.Column("details", pg.JSONB(), nullable=False, server_default="{}")
    )

    op.create_table(
        "model_versions",
        _uuid_pk(),
        sa.Column("model_name", sa.String(64), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("algorithm", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="candidate"),
        sa.Column("mlflow_run_id", sa.String(64)),
        sa.Column("dataset_hash", sa.String(64)),
        _ts("training_window_start"),
        _ts("training_window_end"),
        sa.Column("feature_schema", pg.JSONB(), nullable=False, server_default="{}"),
        sa.Column("hyperparameters", pg.JSONB(), nullable=False, server_default="{}"),
        sa.Column("n_train", sa.Integer()),
        sa.Column("n_test", sa.Integer()),
        sa.Column("is_synthetic_trained", sa.Boolean(), nullable=False),
        sa.Column("artifact_uri", sa.Text()),
        sa.Column("notes", sa.Text()),
        _ts("created_at", nullable=False, default_now=True),
        sa.UniqueConstraint("model_name", "version"),
        sa.CheckConstraint(
            "status IN ('candidate', 'production', 'archived')", name="model_status"
        ),
    )
    op.create_table(
        "model_evaluations",
        _uuid_pk(),
        _fk("model_version_id", "model_versions.id", "CASCADE", nullable=False),
        _ts("evaluated_at", nullable=False, default_now=True),
        sa.Column("split", sa.String(16), nullable=False),
        _ts("window_start"),
        _ts("window_end"),
        sa.Column("metrics", pg.JSONB(), nullable=False),
        sa.Column("n_samples", sa.Integer(), nullable=False),
        sa.Column("data_mode", sa.String(16), nullable=False),
        sa.CheckConstraint("split IN ('validation', 'test', 'live')", name="evaluation_split"),
    )
    op.create_index(
        "ix_model_evaluations_model_evaluated", "model_evaluations",
        ["model_version_id", sa.text("evaluated_at DESC")],
    )  # fmt: skip

    op.create_table(
        "eta_predictions",
        _uuid_pk(),
        _fk("vessel_id", "vessels.id", "CASCADE", nullable=False),
        _fk("target_port_id", "ports.id", "SET NULL"),
        sa.Column("target_unlocode", sa.String(5)),
        _fk("model_version_id", "model_versions.id", "RESTRICT", nullable=False),
        _ts("predicted_at", nullable=False, default_now=True),
        _ts("input_data_as_of", nullable=False),
        _ts("predicted_arrival_at", nullable=False),
        _ts("lower_bound_at"),
        _ts("upper_bound_at"),
        sa.Column("interval_level", sa.Numeric(3, 2)),
        sa.Column("remaining_distance_nm", sa.REAL()),
        sa.Column("features", pg.JSONB(), nullable=False, server_default="{}"),
        _ts("actual_arrival_at"),
        sa.Column("error_hours", sa.REAL()),
        _ts("resolved_at"),
    )
    op.create_index(
        "ix_eta_predictions_vessel_predicted",
        "eta_predictions",
        ["vessel_id", sa.text("predicted_at DESC")],
    )
    op.create_index(
        "ix_eta_predictions_unresolved", "eta_predictions", ["target_port_id"],
        postgresql_where=sa.text("resolved_at IS NULL"),
    )  # fmt: skip

    op.create_table(
        "delay_predictions",
        _uuid_pk(),
        _fk("shipment_id", "shipments.id", "CASCADE", nullable=False),
        _fk("model_version_id", "model_versions.id", "RESTRICT", nullable=False),
        _ts("predicted_at", nullable=False, default_now=True),
        _ts("input_data_as_of", nullable=False),
        sa.Column("delay_probability", sa.REAL(), nullable=False),
        sa.Column("threshold_hours", sa.REAL(), nullable=False),
        sa.Column("calibration_method", sa.String(32)),
        sa.Column("features", pg.JSONB(), nullable=False, server_default="{}"),
        sa.Column("actual_delayed", sa.Boolean()),
        sa.Column("actual_delay_hours", sa.REAL()),
        _ts("resolved_at"),
    )
    op.create_index(
        "ix_delay_predictions_shipment_predicted", "delay_predictions",
        ["shipment_id", sa.text("predicted_at DESC")],
    )  # fmt: skip

    op.create_table(
        "congestion_predictions",
        _uuid_pk(),
        _fk("port_id", "ports.id", "CASCADE", nullable=False),
        sa.Column("metric", sa.String(64), nullable=False),
        _ts("target_time", nullable=False),
        _fk("model_version_id", "model_versions.id", "RESTRICT", nullable=False),
        _ts("predicted_at", nullable=False, default_now=True),
        _ts("input_data_as_of", nullable=False),
        sa.Column("predicted_value", sa.REAL(), nullable=False),
        sa.Column("lower_bound", sa.REAL()),
        sa.Column("upper_bound", sa.REAL()),
        sa.Column("actual_value", sa.REAL()),
        _ts("resolved_at"),
    )
    op.create_index(
        "ix_congestion_predictions_port_target",
        "congestion_predictions",
        ["port_id", "target_time"],
    )

    op.create_table(
        "risk_assessments",
        _uuid_pk(),
        _fk("shipment_id", "shipments.id", "CASCADE"),
        _fk("vessel_id", "vessels.id", "CASCADE"),
        _fk("model_version_id", "model_versions.id", "RESTRICT"),
        _ts("assessed_at", nullable=False, default_now=True),
        sa.Column("score", sa.Numeric(4, 3), nullable=False),
        sa.Column("level", sa.String(8), nullable=False),
        sa.Column("factors", pg.JSONB(), nullable=False, server_default="[]"),
        sa.CheckConstraint("level IN ('low', 'medium', 'high')", name="risk_level"),
    )
    op.create_index(
        "ix_risk_assessments_shipment_assessed", "risk_assessments",
        ["shipment_id", sa.text("assessed_at DESC")],
    )  # fmt: skip

    # Append-only enforcement: compare every column except the allowed outcome columns.
    op.execute(
        """
        CREATE FUNCTION prevent_prediction_rewrite() RETURNS trigger AS $$
        DECLARE
            allowed text[] := TG_ARGV;
            frozen_old jsonb := to_jsonb(OLD) - allowed;
            frozen_new jsonb := to_jsonb(NEW) - allowed;
        BEGIN
            IF frozen_old IS DISTINCT FROM frozen_new THEN
                RAISE EXCEPTION 'predictions are append-only: only outcome columns (%) may change on %',
                    array_to_string(allowed, ', '), TG_TABLE_NAME
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for table, columns in MUTABLE_COLUMNS.items():
        args = ", ".join(f"'{c}'" for c in columns)
        op.execute(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION prevent_prediction_rewrite({args})"
        )


def downgrade() -> None:
    for table in MUTABLE_COLUMNS:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}")
    op.execute("DROP FUNCTION IF EXISTS prevent_prediction_rewrite()")
    for table in (
        "risk_assessments", "congestion_predictions", "delay_predictions", "eta_predictions",
        "model_evaluations", "model_versions",
    ):  # fmt: skip
        op.drop_table(table)
    op.drop_column("system_alerts", "details")
