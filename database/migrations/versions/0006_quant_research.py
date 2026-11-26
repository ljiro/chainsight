"""Quantitative research: indicator series and point-in-time observations (hypertable),
research datasets, maritime features (hypertable), experiments, backtest runs, metrics and
signals; FRED data source.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _uuid_pk() -> sa.Column:
    return sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()"))


def _ts(name: str, nullable: bool = True, default_now: bool = False) -> sa.Column:
    return sa.Column(
        name, sa.DateTime(timezone=True), nullable=nullable,
        server_default=sa.func.now() if default_now else None,
    )  # fmt: skip


def _fk(name: str, target: str, ondelete: str, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.Uuid(), sa.ForeignKey(target, ondelete=ondelete), nullable=nullable)


def _has_timescale() -> bool:
    if context.is_offline_mode():
        return False
    return bool(
        op.get_bind()
        .execute(sa.text("SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'"))
        .scalar()
    )


def upgrade() -> None:
    op.create_table(
        "indicator_series",
        _uuid_pk(),
        sa.Column("key", sa.String(200), nullable=False, unique=True),
        _fk("source_id", "data_sources.id", "RESTRICT"),
        sa.Column("category", sa.String(16), nullable=False),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("entity_kind", sa.String(32), nullable=False),
        sa.Column("entity_key", sa.String(100), nullable=False),
        sa.Column("entity_name", sa.String(200), nullable=False),
        sa.Column("metric", sa.String(100), nullable=False),
        sa.Column("unit", sa.String(50)),
        sa.Column("frequency", sa.String(16), nullable=False, server_default="daily"),
        sa.Column("publication_lag_days", sa.SmallInteger(), nullable=False),
        sa.Column("attributes", pg.JSONB(), nullable=False, server_default="{}"),
        _ts("created_at", nullable=False, default_now=True),
        _ts("updated_at", nullable=False, default_now=True),
        sa.CheckConstraint("category IN ('maritime', 'market')", name="indicator_category"),
    )
    op.create_index("ix_indicator_series_source_id", "indicator_series", ["source_id"])

    op.create_table(
        "indicator_observations",
        _fk("series_id", "indicator_series.id", "CASCADE"),
        _ts("as_of", nullable=False),
        sa.Column("revision", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("value", sa.Double(), nullable=False),
        _ts("available_at", nullable=False),
        sa.Column("available_at_assumed", sa.Boolean(), nullable=False, server_default=sa.false()),
        _ts("ingested_at", nullable=False, default_now=True),
        sa.PrimaryKeyConstraint("series_id", "as_of", "revision"),
    )

    op.create_table(
        "research_datasets",
        _uuid_pk(),
        sa.Column("key", sa.String(100), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("frequency", sa.String(16), nullable=False),
        sa.Column("definition", pg.JSONB(), nullable=False),
        sa.Column("content_hash", sa.String(64)),
        _ts("created_at", nullable=False, default_now=True),
        _ts("updated_at", nullable=False, default_now=True),
    )

    op.create_table(
        "maritime_features",
        sa.Column("feature_key", sa.String(200), nullable=False),
        _ts("as_of", nullable=False),
        sa.Column("version", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("value", sa.Double(), nullable=False),
        _ts("available_at", nullable=False),
        _fk("dataset_id", "research_datasets.id", "CASCADE"),
        _ts("computed_at", nullable=False, default_now=True),
        sa.PrimaryKeyConstraint("feature_key", "as_of", "version"),
    )
    op.create_index("ix_maritime_features_dataset_id", "maritime_features", ["dataset_id"])

    op.create_table(
        "research_experiments",
        _uuid_pk(),
        _fk("organization_id", "organizations.id", "CASCADE"),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("hypothesis", sa.Text(), nullable=False),
        sa.Column("config", pg.JSONB(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        _ts("created_at", nullable=False, default_now=True),
        _ts("updated_at", nullable=False, default_now=True),
        sa.CheckConstraint(
            "status IN ('draft', 'completed', 'failed')", name="research_experiment_status"
        ),
    )
    op.create_index(
        "ix_research_experiments_organization_id", "research_experiments", ["organization_id"]
    )

    op.create_table(
        "backtest_runs",
        _uuid_pk(),
        _fk("experiment_id", "research_experiments.id", "CASCADE"),
        _ts("started_at", nullable=False, default_now=True),
        _ts("finished_at"),
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
        sa.Column("error", sa.Text()),
        sa.Column("config", pg.JSONB(), nullable=False),
        sa.Column("code_version", sa.String(64)),
        sa.Column("data_snapshot_hash", sa.String(64)),
        sa.Column("series", pg.JSONB(), nullable=False, server_default="{}"),
        sa.Column("folds", pg.JSONB(), nullable=False, server_default="[]"),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')", name="backtest_run_status"
        ),
    )
    op.create_index(
        "ix_backtest_runs_experiment_started", "backtest_runs",
        ["experiment_id", sa.text("started_at DESC")],
    )  # fmt: skip

    op.create_table(
        "backtest_metrics",
        _uuid_pk(),
        _fk("backtest_run_id", "backtest_runs.id", "CASCADE"),
        sa.Column("split", sa.String(16), nullable=False),
        sa.Column("metric", sa.String(64), nullable=False),
        sa.Column("value", sa.Double()),
        sa.Column("benchmark_value", sa.Double()),
        sa.UniqueConstraint("backtest_run_id", "split", "metric"),
        sa.CheckConstraint("split IN ('in_sample', 'out_of_sample')", name="backtest_split"),
    )

    op.create_table(
        "research_signals",
        _uuid_pk(),
        _fk("experiment_id", "research_experiments.id", "CASCADE"),
        _fk("backtest_run_id", "backtest_runs.id", "CASCADE"),
        sa.Column("signal_key", sa.String(200), nullable=False),
        _ts("as_of", nullable=False),
        _ts("available_at", nullable=False),
        sa.Column("value", sa.Double()),
        sa.Column("position", sa.Double()),
    )
    op.create_index("ix_research_signals_experiment_id", "research_signals", ["experiment_id"])
    op.create_index(
        "ix_research_signals_run_as_of", "research_signals", ["backtest_run_id", "as_of"]
    )

    if _has_timescale():
        op.execute(
            "SELECT create_hypertable('indicator_observations', 'as_of', "
            "chunk_time_interval => INTERVAL '365 days')"
        )
        op.execute(
            "SELECT create_hypertable('maritime_features', 'as_of', "
            "chunk_time_interval => INTERVAL '365 days')"
        )

    op.get_bind().execute(
        sa.text(
            "INSERT INTO data_sources (key, name, category, data_mode, status, homepage_url, "
            "license_note, attribution, expected_update_interval_seconds) VALUES "
            "('fred', 'FRED (Federal Reserve Bank of St. Louis)', 'market', 'periodic', "
            "'not_configured', 'https://fred.stlouisfed.org', "
            "'Public series downloads; some series carry third-party copyright (e.g. EIA, ICE). "
            "Check each series'' notes before redistribution.', "
            "'Source: FRED, Federal Reserve Bank of St. Louis', 86400) "
            "ON CONFLICT (key) DO NOTHING"
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    for table in (
        "research_signals", "backtest_metrics", "backtest_runs", "research_experiments",
        "maritime_features", "research_datasets", "indicator_observations", "indicator_series",
    ):  # fmt: skip
        op.drop_table(table)
    bind.execute(sa.text("DELETE FROM ingestion_logs WHERE data_source_id IN (SELECT id FROM data_sources WHERE key = 'fred')"))  # fmt: skip
    bind.execute(sa.text("DELETE FROM data_sources WHERE key = 'fred'"))
