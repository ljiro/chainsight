"""Which carrier adapters exist, and what each carrier's integration status honestly is."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import sqlalchemy as sa
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from core.logging_config import get_logger
from core.settings import Settings
from core.timeutils import utcnow
from integrations.carriers.base import CarrierAdapter
from integrations.carriers.dcsa import DcsaCarrierConfig, DcsaTntAdapter
from integrations.carriers.demo import DemoCarrierAdapter

log = get_logger(__name__)

NO_CREDENTIALS = (
    "No credentials configured. A generic DCSA Track & Trace adapter is available: set "
    "CARRIER_DCSA_CONFIG for this carrier (see docs/integrations/carriers.md). It has not been "
    "verified against this carrier's live API."
)
NO_ADAPTER = "No adapter implemented for this provider yet."


@dataclass
class CarrierRegistry:
    adapters: dict[str, CarrierAdapter] = field(default_factory=dict)
    config_errors: dict[str, str] = field(default_factory=dict)

    def get(self, carrier_code: str) -> CarrierAdapter | None:
        return self.adapters.get(carrier_code)


def source_key_for(adapter: CarrierAdapter) -> str:
    return "carrier-demo" if adapter.adapter_key == "demo" else f"carrier-{adapter.carrier_code}"


def build_registry(settings: Settings) -> CarrierRegistry:
    registry = CarrierRegistry(adapters={"demo": DemoCarrierAdapter()})
    if settings.carrier_dcsa_config is None:
        return registry
    try:
        raw = json.loads(settings.carrier_dcsa_config.get_secret_value())
    except ValueError:
        registry.config_errors["*"] = "CARRIER_DCSA_CONFIG is not valid JSON"
        log.error("carrier_config_invalid", reason="invalid JSON")
        return registry
    for code, cfg in (raw or {}).items():
        try:
            registry.adapters[code] = DcsaTntAdapter(code, DcsaCarrierConfig.model_validate(cfg))
        except ValidationError as exc:
            # Never echo the config (it contains secrets): report field paths only.
            fields = ", ".join(".".join(map(str, e["loc"])) for e in exc.errors())
            registry.config_errors[code] = f"invalid configuration: {fields}"
            log.error("carrier_config_invalid", carrier=code, fields=fields)
    return registry


async def ensure_source(session: AsyncSession, adapter: CarrierAdapter) -> None:
    """Real carriers get their own data source row for provenance (created on first use)."""
    key = source_key_for(adapter)
    await session.execute(
        sa.text(
            "INSERT INTO data_sources (key, name, category, data_mode, status, license_note, "
            "attribution) VALUES (:key, :name, 'carrier', :mode, 'not_configured', :lic, :attr) "
            "ON CONFLICT (key) DO NOTHING"
        ),
        {
            "key": key,
            "name": f"{adapter.carrier_code} Track & Trace",
            "mode": adapter.data_mode,
            "lic": "Carrier API terms apply; customer shipment data.",
            "attr": f"Tracking data from {adapter.carrier_code}",
        },
    )


async def sync_integration_statuses(
    session: AsyncSession, registry: CarrierRegistry
) -> dict[str, str]:
    """Recompute platform-level shipment_tracking status for every carrier."""
    carriers = (await session.execute(sa.text("SELECT id, code, carrier_type FROM carriers"))).all()
    result: dict[str, str] = {}
    for c in carriers:
        adapter = registry.get(c.code)
        if c.code in registry.config_errors:
            status, reason, key = "unavailable", registry.config_errors[c.code], "dcsa_tnt_v2"
        elif adapter is not None:
            health = await adapter.health_check()
            status, reason, key = health.status, health.reason, adapter.adapter_key
            await ensure_source(session, adapter)
        elif c.carrier_type == "ocean_carrier":
            status, reason, key = "authentication_required", NO_CREDENTIALS, "dcsa_tnt_v2"
        else:
            status, reason, key = "unavailable", NO_ADAPTER, None
        await session.execute(
            sa.text(
                "INSERT INTO carrier_integrations (carrier_id, organization_id, capability, status, "
                "adapter_key, status_reason, last_checked_at) VALUES (:cid, NULL, "
                "'shipment_tracking', :status, :key, :reason, :now) "
                "ON CONFLICT ON CONSTRAINT uq_carrier_integrations_carrier_id_organization_id_capability "
                "DO UPDATE SET status = EXCLUDED.status, adapter_key = EXCLUDED.adapter_key, "
                "status_reason = EXCLUDED.status_reason, last_checked_at = EXCLUDED.last_checked_at, "
                "updated_at = now()"
            ),
            {"cid": c.id, "status": status, "key": key, "reason": reason, "now": utcnow()},
        )
        result[c.code] = status
    return result
