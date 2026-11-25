"""Reference data: target carriers, platform carrier integrations, external data sources.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-25

Integration statuses are seeded honestly: no carrier adapter exists yet, so every carrier is
`unavailable`, and every external data source is `not_configured` until credentials/collectors
exist. Idempotent (ON CONFLICT DO NOTHING).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CARRIERS = [
    # code, name, scac, carrier_type, website
    ("maersk", "Maersk", "MAEU", "ocean_carrier", "https://www.maersk.com"),
    ("msc", "Mediterranean Shipping Company (MSC)", "MSCU", "ocean_carrier", "https://www.msc.com"),
    ("cma-cgm", "CMA CGM", "CMDU", "ocean_carrier", "https://www.cma-cgm.com"),
    ("hapag-lloyd", "Hapag-Lloyd", "HLCU", "ocean_carrier", "https://www.hapag-lloyd.com"),
    ("cosco", "COSCO Shipping Lines", "COSU", "ocean_carrier", "https://lines.coscoshipping.com"),
    ("evergreen", "Evergreen Marine", "EGLV", "ocean_carrier", "https://www.evergreen-line.com"),
    ("one", "Ocean Network Express (ONE)", "ONEY", "ocean_carrier", "https://www.one-line.com"),
    ("dhl", "DHL Global Forwarding", None, "logistics_provider", "https://www.dhl.com"),
]

DATA_SOURCES = [
    {
        "key": "aisstream",
        "name": "AISStream",
        "category": "ais",
        "data_mode": "real_time",
        "homepage_url": "https://aisstream.io",
        "license_note": "Free service subject to aisstream.io terms; best effort, no SLA; terrestrial receiver coverage.",
        "attribution": "AIS data from aisstream.io",
        "interval": 60,
    },
    {
        "key": "open-meteo",
        "name": "Open-Meteo",
        "category": "weather",
        "data_mode": "real_time",
        "homepage_url": "https://open-meteo.com",
        "license_note": "Data CC BY 4.0. Free API for non-commercial use; commercial use requires a subscription.",
        "attribution": "Weather data by Open-Meteo.com",
        "interval": 3600,
    },
    {
        "key": "imf-portwatch",
        "name": "IMF PortWatch",
        "category": "port",
        "data_mode": "periodic",
        "homepage_url": "https://portwatch.imf.org",
        "license_note": "Open data published by the IMF; verify terms on the portal before redistribution.",
        "attribution": "IMF PortWatch",
        "interval": 604800,
    },
    {
        "key": "un-comtrade",
        "name": "UN Comtrade",
        "category": "trade",
        "data_mode": "periodic",
        "homepage_url": "https://comtradeplus.un.org",
        "license_note": "Free tier requires a registered subscription key; rate-limited; see UN Comtrade terms.",
        "attribution": "United Nations Comtrade database",
        "interval": 2592000,
    },
]


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "INSERT INTO carriers (code, name, scac, carrier_type, website_url) "
            "VALUES (:code, :name, :scac, :carrier_type, :website_url) "
            "ON CONFLICT (code) DO NOTHING"
        ),
        [
            {"code": c, "name": n, "scac": s, "carrier_type": t, "website_url": w}
            for c, n, s, t, w in CARRIERS
        ],
    )
    bind.execute(
        sa.text(
            "INSERT INTO carrier_integrations (carrier_id, organization_id, capability, status, status_reason) "
            "SELECT id, NULL, 'shipment_tracking', 'unavailable', "
            "'No adapter implemented yet (planned for Phase 3).' FROM carriers "
            "ON CONFLICT ON CONSTRAINT uq_carrier_integrations_carrier_id_organization_id_capability DO NOTHING"
        )
    )
    bind.execute(
        sa.text(
            "INSERT INTO data_sources (key, name, category, data_mode, status, homepage_url, "
            "license_note, attribution, expected_update_interval_seconds) "
            "VALUES (:key, :name, :category, :data_mode, 'not_configured', :homepage_url, "
            ":license_note, :attribution, :interval) ON CONFLICT (key) DO NOTHING"
        ),
        DATA_SOURCES,
    )


def downgrade() -> None:
    bind = op.get_bind()
    codes = [c[0] for c in CARRIERS]
    bind.execute(
        sa.text(
            "DELETE FROM carrier_integrations WHERE organization_id IS NULL AND carrier_id IN "
            "(SELECT id FROM carriers WHERE code = ANY(:codes))"
        ),
        {"codes": codes},
    )
    bind.execute(sa.text("DELETE FROM carriers WHERE code = ANY(:codes)"), {"codes": codes})
    bind.execute(
        sa.text("DELETE FROM data_sources WHERE key = ANY(:keys)"),
        {"keys": [d["key"] for d in DATA_SOURCES]},
    )
