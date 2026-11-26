"""Fleet tracking: vessels, identity history, positions (hypertable), voyages, port calls,
port areas, routes, port observations (hypertable), weather; AIS simulator/replay + UN/LOCODE sources.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op
from geoalchemy2 import Geography

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

VESSEL_CLASSES = (
    "cargo", "tanker", "passenger", "fishing", "tug_service", "pleasure", "high_speed",
    "other", "unknown", "container", "bulk", "lng_carrier", "lpg_carrier",
)  # fmt: skip


def _enum(name: str, *values: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True, length=32)


def _uuid_pk() -> sa.Column:
    return sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()"))


def _ts(name: str, nullable: bool = True, default_now: bool = False) -> sa.Column:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=nullable,
        server_default=sa.func.now() if default_now else None,
    )


def _fk(name: str, target: str, ondelete: str, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.Uuid(), sa.ForeignKey(target, ondelete=ondelete), nullable=nullable)


def _geo(kind: str) -> Geography:
    return Geography(geometry_type=kind, srid=4326, spatial_index=False)


def _timescale() -> tuple[bool, bool]:
    """(extension installed, community/TSL features such as compression available)."""
    if context.is_offline_mode():
        return False, False
    bind = op.get_bind()
    installed = bind.execute(
        sa.text("SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'")
    ).scalar()
    if not installed:
        return False, False
    license_ = bind.execute(sa.text("SELECT current_setting('timescaledb.license', true)")).scalar()
    return True, license_ == "timescale"


def upgrade() -> None:
    op.create_table(
        "vessels",
        _uuid_pk(),
        sa.Column("mmsi", sa.Integer()),
        sa.Column("imo", sa.Integer(), unique=True),
        sa.Column("name", sa.String(120)),
        sa.Column("call_sign", sa.String(16)),
        sa.Column("ship_type_code", sa.SmallInteger()),
        sa.Column(
            "vessel_class",
            _enum("vessel_class", *VESSEL_CLASSES),
            nullable=False,
            server_default="unknown",
        ),
        sa.Column("vessel_class_source", sa.String(32)),
        sa.Column("length_m", sa.SmallInteger()),
        sa.Column("beam_m", sa.SmallInteger()),
        sa.Column("draught_m", sa.REAL()),
        sa.Column("destination", sa.String(64)),
        _ts("first_seen_at"),
        _ts("last_seen_at"),
        sa.Column("last_location", _geo("POINT")),
        sa.Column("last_sog_knots", sa.REAL()),
        sa.Column("last_cog_deg", sa.REAL()),
        sa.Column("last_heading_deg", sa.SmallInteger()),
        sa.Column("last_nav_status", sa.SmallInteger()),
        _fk("last_position_source_id", "data_sources.id", "SET NULL"),
        _ts("created_at", nullable=False, default_now=True),
        _ts("updated_at", nullable=False, default_now=True),
    )
    op.create_index(op.f("ix_vessels_mmsi"), "vessels", ["mmsi"])
    op.create_index(
        "ix_vessels_last_location", "vessels", ["last_location"], postgresql_using="gist"
    )
    op.create_index("ix_vessels_last_seen_at", "vessels", [sa.text("last_seen_at DESC")])

    op.create_table(
        "vessel_identifiers",
        _uuid_pk(),
        _fk("vessel_id", "vessels.id", "CASCADE", nullable=False),
        sa.Column(
            "scheme",
            _enum("vessel_identifier_scheme", "mmsi", "imo", "call_sign", "name"),
            nullable=False,
        ),
        sa.Column("value", sa.String(120), nullable=False),
        _ts("valid_from", nullable=False, default_now=True),
        _ts("valid_to"),
        _fk("source_id", "data_sources.id", "SET NULL"),
    )
    op.create_index(op.f("ix_vessel_identifiers_vessel_id"), "vessel_identifiers", ["vessel_id"])
    op.create_index(
        "uq_vessel_identifiers_active_scheme_value",
        "vessel_identifiers",
        ["scheme", "value"],
        unique=True,
        postgresql_where=sa.text("valid_to IS NULL AND scheme IN ('mmsi', 'imo')"),
    )

    op.create_table(
        "vessel_carrier_history",
        _uuid_pk(),
        _fk("vessel_id", "vessels.id", "CASCADE", nullable=False),
        _fk("carrier_id", "carriers.id", "RESTRICT", nullable=False),
        sa.Column(
            "relation",
            _enum("carrier_relation", "operator", "owner", "charterer", "slot"),
            nullable=False,
        ),
        sa.Column(
            "method",
            _enum("association_method", "name_heuristic", "manual", "carrier_api"),
            nullable=False,
        ),
        sa.Column("confidence", sa.Numeric(3, 2), nullable=False),
        _ts("valid_from", nullable=False, default_now=True),
        _ts("valid_to"),
        _fk("source_id", "data_sources.id", "SET NULL"),
        _ts("created_at", nullable=False, default_now=True),
    )
    op.create_index(
        op.f("ix_vessel_carrier_history_vessel_id"), "vessel_carrier_history", ["vessel_id"]
    )
    op.create_index(
        op.f("ix_vessel_carrier_history_carrier_id"), "vessel_carrier_history", ["carrier_id"]
    )

    op.create_table(
        "vessel_positions",
        sa.Column(
            "vessel_id",
            sa.Uuid(),
            sa.ForeignKey("vessels.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("observed_at", sa.DateTime(timezone=True), primary_key=True),
        sa.Column(
            "source_id",
            sa.Uuid(),
            sa.ForeignKey("data_sources.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        _ts("ingested_at", nullable=False, default_now=True),
        sa.Column("location", _geo("POINT"), nullable=False),
        sa.Column("sog_knots", sa.REAL()),
        sa.Column("cog_deg", sa.REAL()),
        sa.Column("heading_deg", sa.SmallInteger()),
        sa.Column("nav_status", sa.SmallInteger()),
        sa.Column("rate_of_turn", sa.REAL()),
        sa.Column("position_accuracy_high", sa.Boolean()),
        sa.Column("message_type", sa.String(48), nullable=False),
        sa.Column("quality_flags", sa.SmallInteger(), nullable=False, server_default="0"),
    )

    op.create_table(
        "vessel_position_sources",
        _uuid_pk(),
        _fk("ingestion_log_id", "ingestion_logs.id", "CASCADE", nullable=False),
        _fk("data_source_id", "data_sources.id", "RESTRICT", nullable=False),
        sa.Column("receiver_type", sa.String(16), nullable=False),
        _ts("window_start", nullable=False),
        _ts("window_end", nullable=False),
        sa.Column("messages", sa.Integer(), nullable=False),
        sa.Column("positions", sa.Integer(), nullable=False),
        sa.Column("mean_latency_ms", sa.REAL()),
    )
    op.create_index(
        op.f("ix_vessel_position_sources_ingestion_log_id"),
        "vessel_position_sources",
        ["ingestion_log_id"],
    )

    op.create_table(
        "voyages",
        _uuid_pk(),
        _fk("vessel_id", "vessels.id", "CASCADE", nullable=False),
        _fk("carrier_id", "carriers.id", "SET NULL"),
        sa.Column("voyage_number", sa.String(32)),
        sa.Column("service_code", sa.String(32)),
        _fk("origin_port_id", "ports.id", "RESTRICT"),
        _fk("destination_port_id", "ports.id", "RESTRICT"),
        _ts("departed_at"),
        _ts("arrived_at"),
        sa.Column(
            "status",
            _enum("voyage_status", "planned", "underway", "completed", "cancelled"),
            nullable=False,
        ),
        _fk("source_id", "data_sources.id", "SET NULL"),
        _ts("created_at", nullable=False, default_now=True),
        _ts("updated_at", nullable=False, default_now=True),
    )
    op.create_index(
        "ix_voyages_vessel_departed", "voyages", ["vessel_id", sa.text("departed_at DESC")]
    )

    op.create_table(
        "voyage_port_calls",
        _uuid_pk(),
        _fk("vessel_id", "vessels.id", "CASCADE", nullable=False),
        _fk("voyage_id", "voyages.id", "SET NULL"),
        _fk("port_id", "ports.id", "RESTRICT", nullable=False),
        sa.Column("sequence", sa.SmallInteger()),
        _ts("planned_arrival_at"),
        _ts("estimated_arrival_at"),
        _ts("actual_arrival_at"),
        _ts("planned_departure_at"),
        _ts("estimated_departure_at"),
        _ts("actual_departure_at"),
        sa.Column(
            "detection_method",
            _enum("port_call_detection", "carrier_schedule", "ais_geofence"),
            nullable=False,
        ),
        _fk("source_id", "data_sources.id", "SET NULL"),
        _ts("created_at", nullable=False, default_now=True),
    )
    op.create_index(
        "ix_voyage_port_calls_vessel_arrival",
        "voyage_port_calls",
        ["vessel_id", sa.text("actual_arrival_at DESC")],
    )
    op.create_index(
        "ix_voyage_port_calls_port_arrival",
        "voyage_port_calls",
        ["port_id", sa.text("actual_arrival_at DESC")],
    )

    op.create_table(
        "port_areas",
        _uuid_pk(),
        _fk("port_id", "ports.id", "CASCADE", nullable=False),
        sa.Column("area", _geo("POLYGON"), nullable=False),
        sa.Column("radius_m", sa.Integer()),
        sa.Column("method", sa.String(32), nullable=False),
        _ts("created_at", nullable=False, default_now=True),
    )
    op.create_index(op.f("ix_port_areas_port_id"), "port_areas", ["port_id"])
    op.create_index("ix_port_areas_area", "port_areas", ["area"], postgresql_using="gist")

    op.create_table(
        "maritime_routes",
        _uuid_pk(),
        sa.Column("name", sa.String(200), nullable=False),
        _fk("origin_port_id", "ports.id", "RESTRICT"),
        _fk("destination_port_id", "ports.id", "RESTRICT"),
        sa.Column("path", _geo("LINESTRING"), nullable=False),
        sa.Column("distance_nm", sa.REAL()),
        sa.Column("source", sa.Text()),
        _ts("created_at", nullable=False, default_now=True),
    )

    op.create_table(
        "port_observations",
        sa.Column(
            "port_id", sa.Uuid(), sa.ForeignKey("ports.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("metric", sa.String(64), primary_key=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), primary_key=True),
        sa.Column(
            "source_id",
            sa.Uuid(),
            sa.ForeignKey("data_sources.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        _ts("available_at", nullable=False),
        sa.Column("value", sa.Double(), nullable=False),
        _ts("ingested_at", nullable=False, default_now=True),
    )

    op.create_table(
        "weather_observations",
        _uuid_pk(),
        _fk("port_id", "ports.id", "CASCADE"),
        sa.Column("location", _geo("POINT"), nullable=False),
        sa.Column(
            "kind", _enum("weather_kind", "current", "forecast", "historical"), nullable=False
        ),
        _ts("valid_at", nullable=False),
        _ts("retrieved_at", nullable=False, default_now=True),
        sa.Column("temperature_c", sa.REAL()),
        sa.Column("wind_speed_kn", sa.REAL()),
        sa.Column("wind_direction_deg", sa.REAL()),
        sa.Column("wind_gusts_kn", sa.REAL()),
        sa.Column("precipitation_mm", sa.REAL()),
        _fk("source_id", "data_sources.id", "RESTRICT", nullable=False),
        sa.UniqueConstraint("port_id", "kind", "valid_at", "source_id"),
    )
    op.create_index(
        "ix_weather_observations_port_valid",
        "weather_observations",
        ["port_id", sa.text("valid_at DESC")],
    )

    has_timescale, has_compression = _timescale()
    if has_timescale:
        op.execute(
            "SELECT create_hypertable('vessel_positions', 'observed_at', "
            "chunk_time_interval => INTERVAL '1 day')"
        )
        op.execute(
            "SELECT create_hypertable('port_observations', 'observed_at', "
            "chunk_time_interval => INTERVAL '30 days')"
        )
    else:
        # Plain-PostGIS fallback: keep time-ordered access fast without hypertables.
        op.create_index(
            "ix_vessel_positions_observed_at", "vessel_positions", [sa.text("observed_at DESC")]
        )
    if has_compression:
        op.execute(
            "ALTER TABLE vessel_positions SET (timescaledb.compress, "
            "timescaledb.compress_segmentby = 'vessel_id', "
            "timescaledb.compress_orderby = 'observed_at DESC')"
        )
        op.execute("SELECT add_compression_policy('vessel_positions', INTERVAL '7 days')")

    bind = op.get_bind()
    bind.execute(
        sa.text(
            "INSERT INTO data_sources (key, name, category, data_mode, status, homepage_url, "
            "license_note, attribution, expected_update_interval_seconds) VALUES "
            "(:key, :name, :category, :data_mode, 'not_configured', :homepage_url, :license_note, "
            ":attribution, :interval) ON CONFLICT (key) DO NOTHING"
        ),
        [
            {
                "key": "ais-simulator",
                "name": "AIS simulator",
                "category": "ais",
                "data_mode": "synthetic",
                "homepage_url": None,
                "license_note": "Generated in real time by integrations/ais/simulator.py. Not real vessel data.",
                "attribution": "Synthetic data generated by ChainSight",
                "interval": 60,
            },
            {
                "key": "ais-replay",
                "name": "AIS replay (recorded feed)",
                "category": "ais",
                "data_mode": "historical",
                "homepage_url": None,
                "license_note": "Replays a recording of a real AIS feed; the original provider's terms apply.",
                "attribution": "Original AIS provider of the recording",
                "interval": None,
            },
            {
                "key": "un-locode",
                "name": "UN/LOCODE port registry",
                "category": "port",
                "data_mode": "historical",
                "homepage_url": "https://unece.org/trade/uncefact/unlocode",
                "license_note": "UN/LOCODE is maintained by UNECE. Downloaded at import time and not bundled in this repository.",
                "attribution": "UN/LOCODE, UNECE",
                "interval": None,
            },
        ],
    )


def downgrade() -> None:
    op.execute("DELETE FROM data_sources WHERE key IN ('ais-simulator', 'ais-replay', 'un-locode')")
    for table in (
        "weather_observations",
        "port_observations",
        "maritime_routes",
        "port_areas",
        "voyage_port_calls",
        "voyages",
        "vessel_position_sources",
        "vessel_positions",
        "vessel_carrier_history",
        "vessel_identifiers",
        "vessels",
    ):
        op.drop_table(table)
