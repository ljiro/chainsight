"""Shipment tracking: shipments, containers, items, vessel legs, append-only events.
Seeds the single local organization (until auth), a clearly-labelled demo carrier, and the
carrier-demo (synthetic) and csv-import (historical) data sources.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCAL_ORG_SLUG = "local"
DEMO_CARRIER = "demo"
SOURCES = ("carrier-demo", "csv-import")


def _enum(name: str, *values: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True, length=32)


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
    op.create_table(
        "shipments",
        _uuid_pk(),
        _fk("organization_id", "organizations.id", "CASCADE", nullable=False),
        _fk("carrier_id", "carriers.id", "RESTRICT", nullable=False),
        sa.Column(
            "reference_type", _enum("reference_type", "container", "booking", "bill_of_lading"),
            nullable=False,
        ),
        sa.Column("reference_value", sa.String(64), nullable=False),
        sa.Column("origin_unlocode", sa.String(5)),
        sa.Column("destination_unlocode", sa.String(5)),
        _fk("origin_port_id", "ports.id", "SET NULL"),
        _fk("destination_port_id", "ports.id", "SET NULL"),
        sa.Column(
            "status",
            _enum("shipment_status", "unknown", "planned", "at_origin", "loaded", "in_transit",
                  "transshipment", "discharged", "delivered"),
            nullable=False, server_default="unknown",
        ),
        _ts("planned_arrival_at"),
        _ts("current_eta_at"),
        _ts("actual_arrival_at"),
        sa.Column("delay_hours", sa.Numeric(8, 2)),
        sa.Column(
            "delay_status",
            _enum("delay_status", "unknown", "on_time", "delayed", "arrived_on_time", "arrived_late"),
            nullable=False, server_default="unknown",
        ),
        _ts("last_event_at"),
        _ts("last_refreshed_at"),
        _fk("source_id", "data_sources.id", "RESTRICT", nullable=False),
        _ts("created_at", nullable=False, default_now=True),
        _ts("updated_at", nullable=False, default_now=True),
        sa.UniqueConstraint("organization_id", "carrier_id", "reference_type", "reference_value"),
    )  # fmt: skip
    op.create_index(op.f("ix_shipments_carrier_id"), "shipments", ["carrier_id"])
    op.create_index(
        "ix_shipments_org_updated", "shipments", ["organization_id", sa.text("updated_at DESC")]
    )

    op.create_table(
        "containers",
        _uuid_pk(),
        _fk("organization_id", "organizations.id", "CASCADE", nullable=False),
        sa.Column("container_number", sa.String(11), nullable=False),
        sa.Column("iso_size_type", sa.String(4)),
        sa.Column("check_digit_valid", sa.Boolean(), nullable=False),
        _ts("created_at", nullable=False, default_now=True),
        sa.UniqueConstraint("organization_id", "container_number"),
    )
    op.create_table(
        "shipment_items",
        _uuid_pk(),
        _fk("shipment_id", "shipments.id", "CASCADE", nullable=False),
        _fk("container_id", "containers.id", "CASCADE", nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("weight_kg", sa.Numeric(12, 2)),
        sa.Column("hs_code", sa.String(10)),
        sa.UniqueConstraint("shipment_id", "container_id"),
    )
    op.create_index(op.f("ix_shipment_items_container_id"), "shipment_items", ["container_id"])
    op.create_table(
        "shipment_vessels",
        _uuid_pk(),
        _fk("shipment_id", "shipments.id", "CASCADE", nullable=False),
        _fk("vessel_id", "vessels.id", "SET NULL"),
        sa.Column("leg_sequence", sa.SmallInteger(), nullable=False),
        sa.Column("carrier_vessel_name", sa.String(120)),
        sa.Column("carrier_vessel_imo", sa.Integer()),
        sa.Column("carrier_voyage_number", sa.String(32)),
        sa.Column("load_unlocode", sa.String(5)),
        sa.Column("discharge_unlocode", sa.String(5)),
        _fk("source_id", "data_sources.id", "RESTRICT", nullable=False),
        _ts("updated_at", nullable=False, default_now=True),
        sa.UniqueConstraint("shipment_id", "leg_sequence"),
    )
    op.create_index(op.f("ix_shipment_vessels_vessel_id"), "shipment_vessels", ["vessel_id"])
    op.create_table(
        "shipment_events",
        _uuid_pk(),
        _fk("shipment_id", "shipments.id", "CASCADE", nullable=False),
        _fk("container_id", "containers.id", "SET NULL"),
        sa.Column(
            "event_type",
            _enum("shipment_event_type", "equipment", "transport", "shipment"),
            nullable=False,
        ),
        sa.Column("event_code", sa.String(16), nullable=False),
        sa.Column(
            "classifier",
            _enum("event_classifier", "PLANNED", "ESTIMATED", "ACTUAL"),
            nullable=False,
        ),
        _ts("event_at", nullable=False),
        _ts("event_created_at"),
        sa.Column("location_unlocode", sa.String(5)),
        sa.Column("location_name", sa.String(200)),
        sa.Column("vessel_imo", sa.Integer()),
        sa.Column("vessel_name", sa.String(120)),
        sa.Column("voyage_number", sa.String(32)),
        sa.Column("transport_mode", sa.String(16)),
        sa.Column("description", sa.Text()),
        sa.Column("raw_code", sa.String(64)),
        _fk("source_id", "data_sources.id", "RESTRICT", nullable=False),
        sa.Column("source_event_id", sa.String(128)),
        sa.Column("dedupe_key", sa.String(128), nullable=False),
        _ts("ingested_at", nullable=False, default_now=True),
        sa.UniqueConstraint("shipment_id", "dedupe_key"),
    )
    op.create_index(
        "ix_shipment_events_shipment_time", "shipment_events", ["shipment_id", "event_at"]
    )

    bind = op.get_bind()
    bind.execute(
        sa.text(
            "INSERT INTO organizations (name, slug) VALUES ('Local workspace', :slug) "
            "ON CONFLICT (slug) DO NOTHING"
        ),
        {"slug": LOCAL_ORG_SLUG},
    )
    bind.execute(
        sa.text(
            "INSERT INTO carriers (code, name, scac, carrier_type, website_url) VALUES "
            "(:code, 'ChainSight Demo Carrier (synthetic)', NULL, 'ocean_carrier', NULL) "
            "ON CONFLICT (code) DO NOTHING"
        ),
        {"code": DEMO_CARRIER},
    )
    bind.execute(
        sa.text(
            "INSERT INTO carrier_integrations (carrier_id, organization_id, capability, status, "
            "adapter_key, status_reason) SELECT id, NULL, 'shipment_tracking', 'demo_data_only', "
            "'demo', 'Synthetic shipments generated by the demo adapter for development.' "
            "FROM carriers WHERE code = :code ON CONFLICT ON CONSTRAINT "
            "uq_carrier_integrations_carrier_id_organization_id_capability DO NOTHING"
        ),
        {"code": DEMO_CARRIER},
    )
    bind.execute(
        sa.text(
            "INSERT INTO data_sources (key, name, category, data_mode, status, license_note, "
            "attribution) VALUES (:key, :name, 'carrier', :mode, 'not_configured', :lic, :attr) "
            "ON CONFLICT (key) DO NOTHING"
        ),
        [
            {
                "key": "carrier-demo", "name": "Demo carrier (synthetic)", "mode": "synthetic",
                "lic": "Generated by integrations/carriers/demo.py. Not real shipments.",
                "attr": "Synthetic data generated by ChainSight",
            },
            {
                "key": "csv-import", "name": "CSV shipment import", "mode": "historical",
                "lic": "User-provided shipment data; ownership and terms belong to the uploader.",
                "attr": "Uploaded by the workspace",
            },
        ],
    )  # fmt: skip


def downgrade() -> None:
    for table in (
        "shipment_events",
        "shipment_vessels",
        "shipment_items",
        "containers",
        "shipments",
    ):
        op.drop_table(table)
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "DELETE FROM carrier_integration_logs WHERE carrier_integration_id IN (SELECT ci.id "
            "FROM carrier_integrations ci JOIN carriers c ON c.id = ci.carrier_id WHERE c.code = :c)"
        ),
        {"c": DEMO_CARRIER},
    )
    bind.execute(
        sa.text(
            "DELETE FROM carrier_integrations WHERE carrier_id IN "
            "(SELECT id FROM carriers WHERE code = :c)"
        ),
        {"c": DEMO_CARRIER},
    )
    bind.execute(sa.text("DELETE FROM carriers WHERE code = :c"), {"c": DEMO_CARRIER})
    bind.execute(
        sa.text(
            "DELETE FROM ingestion_logs WHERE data_source_id IN "
            "(SELECT id FROM data_sources WHERE key = ANY(:k))"
        ),
        {"k": list(SOURCES)},
    )
    bind.execute(sa.text("DELETE FROM data_sources WHERE key = ANY(:k)"), {"k": list(SOURCES)})
    # The local organization may own other rows (members, keys); leave it in place.
