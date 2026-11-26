from __future__ import annotations

from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

import database.models  # noqa: F401
from database.base import Base
from database.migrations_support import compare_type, include_object

OUR_TABLES = set(Base.metadata.tables)


def _public_tables(engine: Engine) -> set[str]:
    return set(sa.inspect(engine).get_table_names(schema="public"))


def test_roundtrip_upgrade_downgrade_upgrade(migrated_db: Engine, alembic_config: Config) -> None:
    assert OUR_TABLES <= _public_tables(migrated_db)

    command.downgrade(alembic_config, "base")
    migrated_db.dispose()
    assert not (OUR_TABLES & _public_tables(migrated_db))

    command.upgrade(alembic_config, "head")
    migrated_db.dispose()
    assert OUR_TABLES <= _public_tables(migrated_db)


def test_models_match_migrations(migrated_db: Engine) -> None:
    with migrated_db.connect() as conn:
        ctx = MigrationContext.configure(
            conn,
            opts={
                "include_object": include_object,
                "compare_type": compare_type,
                "target_metadata": Base.metadata,
            },
        )
        diff = compare_metadata(ctx, Base.metadata)
    assert diff == [], f"Model/migration drift detected:\n{diff}"


def test_postgis_enabled(migrated_db: Engine) -> None:
    with migrated_db.connect() as conn:
        version = conn.execute(sa.text("SELECT postgis_version()")).scalar()
    assert version


def test_reference_data_seeded(migrated_db: Engine) -> None:
    with migrated_db.connect() as conn:
        carriers = conn.execute(sa.text("SELECT code, scac FROM carriers ORDER BY code")).all()
        integrations = conn.execute(
            sa.text(
                "SELECT c.code, ci.capability, ci.status FROM carrier_integrations ci "
                "JOIN carriers c ON c.id = ci.carrier_id WHERE ci.organization_id IS NULL"
            )
        ).all()
        sources = conn.execute(sa.text("SELECT key, status, data_mode FROM data_sources")).all()

    codes = {c.code for c in carriers}
    assert codes == {
        "maersk",
        "msc",
        "cma-cgm",
        "hapag-lloyd",
        "cosco",
        "evergreen",
        "one",
        "dhl",
        "demo",
    }
    # Freshly migrated, nothing may claim to be connected: real carriers are unavailable
    # and only the demo carrier is demo_data_only.
    statuses = {i.code: i.status for i in integrations}
    assert statuses.pop("demo") == "demo_data_only"
    assert set(statuses.values()) == {"unavailable"}
    assert len(integrations) == len(codes)
    assert {s.key for s in sources} == {
        "aisstream",
        "open-meteo",
        "imf-portwatch",
        "un-comtrade",
        "ais-simulator",
        "ais-replay",
        "un-locode",
        "carrier-demo",
        "csv-import",
    }
    assert {s.status for s in sources} == {"not_configured"}


def test_platform_integration_unique_even_with_null_org(migrated_db: Engine) -> None:
    with migrated_db.connect() as conn, pytest.raises(IntegrityError):
        conn.execute(
            sa.text(
                "INSERT INTO carrier_integrations "
                "(carrier_id, organization_id, capability, status) "
                "SELECT id, NULL, 'shipment_tracking', 'unavailable' "
                "FROM carriers WHERE code = 'maersk'"
            )
        )


def test_enum_check_constraint_enforced(migrated_db: Engine) -> None:
    with migrated_db.connect() as conn, pytest.raises(IntegrityError):
        conn.execute(
            sa.text(
                "INSERT INTO data_sources (key, name, category, data_mode) "
                "VALUES ('x', 'X', 'ais', 'made_up_mode')"
            )
        )


def test_only_one_open_alert_per_dedupe_key(migrated_db: Engine) -> None:
    insert = sa.text(
        "INSERT INTO system_alerts (severity, category, title, dedupe_key, resolved_at) "
        "VALUES ('warning', 'ingestion', 'stale', 'k1', :resolved)"
    )
    with migrated_db.begin() as conn:
        conn.execute(insert, {"resolved": datetime(2026, 1, 1, tzinfo=UTC)})
        conn.execute(insert, {"resolved": None})
    with migrated_db.connect() as conn, pytest.raises(IntegrityError):
        conn.execute(insert, {"resolved": None})
    with migrated_db.begin() as conn:
        conn.execute(sa.text("DELETE FROM system_alerts WHERE dedupe_key = 'k1'"))


def test_port_geography_roundtrip(migrated_db: Engine) -> None:
    with migrated_db.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO ports (unlocode, name, country_code, location) "
                "VALUES ('ZZTST', 'Test Port', 'ZZ', ST_GeogFromText('SRID=4326;POINT(4.0 51.9)'))"
            )
        )
        # Distance query exercises geography + GiST index path.
        d = conn.execute(
            sa.text(
                "SELECT ST_Distance(location, ST_GeogFromText('SRID=4326;POINT(4.0 52.0)')) "
                "FROM ports WHERE unlocode = 'ZZTST'"
            )
        ).scalar()
        conn.execute(sa.text("DELETE FROM ports WHERE unlocode = 'ZZTST'"))
    assert d is not None and 11_000 < d < 11_200  # ~0.1° latitude ≈ 11.1 km
