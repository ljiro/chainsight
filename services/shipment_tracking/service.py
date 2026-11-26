"""Shipment tracking orchestration and persistence.

track() -> adapter -> normalized events -> store (append-only) -> derive milestones.
Every provider call is written to carrier_integration_logs; integration status follows the
outcome (auth failures flip it to authentication_required, successes confirm connected).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from core.logging_config import get_logger
from core.timeutils import utcnow
from database.enums import ReferenceType
from integrations.carriers.dcsa import event_fingerprint
from integrations.carriers.models import (
    CallRecord,
    CarrierAuthError,
    CarrierError,
    NormalizedEvent,
    TrackingReference,
    TrackingResult,
)
from integrations.carriers.references import is_valid_container_number, normalize_reference
from services.shipment_tracking.milestones import derive_milestones
from services.shipment_tracking.registry import CarrierRegistry, ensure_source, source_key_for

log = get_logger(__name__)

LOCAL_ORG_SLUG = "local"


class CarrierNotFound(LookupError):
    pass


class IntegrationNotAvailable(Exception):
    def __init__(self, carrier_code: str, status: str, reason: str | None) -> None:
        super().__init__(reason or status)
        self.carrier_code, self.status, self.reason = carrier_code, status, reason


@dataclass(frozen=True)
class StoreOutcome:
    shipment_id: uuid.UUID
    created: bool
    events_inserted: int
    events_duplicate: int


async def local_org_id(session: AsyncSession) -> uuid.UUID:
    """Single-workspace mode until authentication (Phase 6) supplies the caller's org."""
    org: uuid.UUID = (
        await session.execute(
            sa.text("SELECT id FROM organizations WHERE slug = :s"), {"s": LOCAL_ORG_SLUG}
        )
    ).scalar_one()
    return org


async def _carrier(
    session: AsyncSession, code: str
) -> tuple[uuid.UUID, uuid.UUID | None, str, str | None]:
    row = (
        await session.execute(
            sa.text(
                "SELECT c.id, ci.id AS integration_id, COALESCE(ci.status, 'unavailable') AS status, "
                "ci.status_reason FROM carriers c LEFT JOIN carrier_integrations ci ON ci.carrier_id = c.id "
                "AND ci.organization_id IS NULL AND ci.capability = 'shipment_tracking' WHERE c.code = :c"
            ),
            {"c": code},
        )
    ).one_or_none()
    if row is None:
        raise CarrierNotFound(code)
    return row.id, row.integration_id, row.status, row.status_reason


async def _log_calls(
    session: AsyncSession, integration_id: uuid.UUID | None, calls: list[CallRecord]
) -> None:
    if integration_id is None or not calls:
        return
    await session.execute(
        sa.text(
            "INSERT INTO carrier_integration_logs (carrier_integration_id, occurred_at, operation, "
            "outcome, http_status, latency_ms, error_code, message, provider_request_id) VALUES "
            "(:iid, :occurred_at, :operation, :outcome, :http_status, :latency_ms, :error_code, "
            ":message, :provider_request_id)"
        ),
        [{"iid": integration_id, **c.model_dump()} for c in calls],
    )


async def _source_id(session: AsyncSession, key: str) -> uuid.UUID:
    sid: uuid.UUID = (
        await session.execute(sa.text("SELECT id FROM data_sources WHERE key = :k"), {"k": key})
    ).scalar_one()
    return sid


async def store_result(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    carrier_id: uuid.UUID,
    reference: TrackingReference,
    events: list[NormalizedEvent],
    source_id: uuid.UUID,
    threshold_hours: float,
    origin: str | None = None,
    destination: str | None = None,
) -> StoreOutcome:
    ref_value = normalize_reference(reference.value)
    now = utcnow()
    row = (
        await session.execute(
            sa.text(
                "INSERT INTO shipments (organization_id, carrier_id, reference_type, reference_value, "
                "source_id, origin_unlocode, destination_unlocode) VALUES (:org, :carrier, :rtype, "
                ":rval, :source, :origin, :dest) ON CONFLICT (organization_id, carrier_id, "
                "reference_type, reference_value) DO UPDATE SET updated_at = now(), "
                "origin_unlocode = COALESCE(EXCLUDED.origin_unlocode, shipments.origin_unlocode), "
                "destination_unlocode = COALESCE(EXCLUDED.destination_unlocode, "
                "shipments.destination_unlocode) RETURNING id, (xmax = 0) AS created"
            ),
            {
                "org": org_id,
                "carrier": carrier_id,
                "rtype": reference.reference_type,
                "rval": ref_value,
                "source": source_id,
                "origin": origin,
                "dest": destination,
            },
        )
    ).one()
    shipment_id = row.id

    # Containers (validated, flagged when the check digit fails) and shipment items.
    container_ids: dict[str, uuid.UUID] = {}
    numbers = {e.container_number for e in events if e.container_number}
    if reference.reference_type == ReferenceType.CONTAINER.value:
        numbers.add(ref_value)
    for number in sorted(numbers):
        cid = (
            await session.execute(
                sa.text(
                    "INSERT INTO containers (organization_id, container_number, check_digit_valid) "
                    "VALUES (:org, :n, :valid) ON CONFLICT (organization_id, container_number) "
                    "DO UPDATE SET check_digit_valid = EXCLUDED.check_digit_valid RETURNING id"
                ),
                {"org": org_id, "n": number[:11], "valid": is_valid_container_number(number)},
            )
        ).scalar_one()
        container_ids[number] = cid
        await session.execute(
            sa.text(
                "INSERT INTO shipment_items (shipment_id, container_id) VALUES (:s, :c) "
                "ON CONFLICT (shipment_id, container_id) DO NOTHING"
            ),
            {"s": shipment_id, "c": cid},
        )

    # Append-only events, deduplicated by provider id or a content fingerprint.
    rows: list[dict[str, Any]] = []
    for e in events:
        rows.append(
            {
                "container_id": (
                    str(container_ids[e.container_number])
                    if e.container_number in container_ids
                    else None
                ),
                "event_type": e.event_type, "event_code": e.event_code, "classifier": e.classifier,
                "event_at": e.event_at.isoformat(),
                "event_created_at": e.event_created_at.isoformat() if e.event_created_at else None,
                "location_unlocode": e.location_unlocode, "location_name": e.location_name,
                "vessel_imo": e.vessel_imo, "vessel_name": e.vessel_name,
                "voyage_number": e.voyage_number, "transport_mode": e.transport_mode,
                "description": e.description, "raw_code": e.raw_code,
                "source_event_id": e.source_event_id,
                "dedupe_key": (e.source_event_id or event_fingerprint(e))[:128],
            }
        )  # fmt: skip
    inserted = 0
    if rows:
        inserted = len(
            (
                await session.execute(
                    sa.text(
                        """
                        INSERT INTO shipment_events (shipment_id, container_id, event_type, event_code,
                            classifier, event_at, event_created_at, location_unlocode, location_name,
                            vessel_imo, vessel_name, voyage_number, transport_mode, description, raw_code,
                            source_id, source_event_id, dedupe_key)
                        SELECT :sid, r.container_id, r.event_type, r.event_code, r.classifier, r.event_at,
                            r.event_created_at, r.location_unlocode, r.location_name, r.vessel_imo,
                            r.vessel_name, r.voyage_number, r.transport_mode, r.description, r.raw_code,
                            :source, r.source_event_id, r.dedupe_key
                        FROM jsonb_to_recordset(CAST(:rows AS jsonb)) AS r(container_id uuid,
                            event_type text, event_code text, classifier text, event_at timestamptz,
                            event_created_at timestamptz, location_unlocode text, location_name text,
                            vessel_imo int, vessel_name text, voyage_number text, transport_mode text,
                            description text, raw_code text, source_event_id text, dedupe_key text)
                        ON CONFLICT (shipment_id, dedupe_key) DO NOTHING
                        RETURNING id
                        """
                    ),
                    {"sid": shipment_id, "source": source_id, "rows": json.dumps(rows)},
                )
            ).all()
        )

    await recompute(session, shipment_id, threshold_hours=threshold_hours, refreshed_at=now)
    return StoreOutcome(shipment_id, bool(row.created), inserted, len(rows) - inserted)


@dataclass(frozen=True)
class _StoredEvent:
    event_type: str
    event_code: str
    classifier: str
    event_at: datetime
    event_created_at: datetime | None
    location_unlocode: str | None
    vessel_name: str | None
    vessel_imo: int | None
    voyage_number: str | None


async def recompute(
    session: AsyncSession,
    shipment_id: uuid.UUID,
    *,
    threshold_hours: float,
    refreshed_at: datetime | None = None,
) -> None:
    """Re-derive status, ETA, delay and legs from all stored events."""
    ship = (
        await session.execute(
            sa.text(
                "SELECT planned_arrival_at, origin_unlocode, destination_unlocode, source_id "
                "FROM shipments WHERE id = :id"
            ),
            {"id": shipment_id},
        )
    ).one()
    stored = [
        _StoredEvent(**r._mapping)
        for r in await session.execute(
            sa.text(
                "SELECT event_type, event_code, classifier, event_at, "
                "COALESCE(event_created_at, ingested_at) AS event_created_at, location_unlocode, "
                "vessel_name, vessel_imo, voyage_number FROM shipment_events WHERE shipment_id = :id"
            ),
            {"id": shipment_id},
        )
    ]
    m = derive_milestones(
        stored,
        origin=ship.origin_unlocode,
        destination=ship.destination_unlocode,
        stored_planned_arrival=ship.planned_arrival_at,
        threshold_hours=threshold_hours,
    )
    await session.execute(
        sa.text(
            """
            UPDATE shipments SET
                origin_unlocode = CAST(:origin AS varchar), destination_unlocode = CAST(:dest AS varchar),
                origin_port_id = (SELECT id FROM ports WHERE unlocode = CAST(:origin AS varchar)),
                destination_port_id = (SELECT id FROM ports WHERE unlocode = CAST(:dest AS varchar)),
                status = :status,
                planned_arrival_at = COALESCE(planned_arrival_at, :planned),  -- write-once
                current_eta_at = :eta, actual_arrival_at = :actual,
                delay_hours = :delay, delay_status = :delay_status,
                last_event_at = :last_event,
                last_refreshed_at = COALESCE(:refreshed, last_refreshed_at),
                updated_at = now()
            WHERE id = :id
            """
        ),
        {
            "id": shipment_id, "origin": m.origin, "dest": m.destination, "status": m.status.value,
            "planned": m.planned_arrival_at, "eta": m.current_eta_at, "actual": m.actual_arrival_at,
            "delay": m.delay_hours, "delay_status": m.delay_status.value,
            "last_event": m.last_event_at, "refreshed": refreshed_at,
        },
    )  # fmt: skip
    for leg in m.legs:
        await session.execute(
            sa.text(
                """
                INSERT INTO shipment_vessels (shipment_id, vessel_id, leg_sequence, carrier_vessel_name,
                    carrier_vessel_imo, carrier_voyage_number, load_unlocode, discharge_unlocode, source_id)
                VALUES (:sid, (SELECT id FROM vessels WHERE imo = CAST(:imo AS int)), :seq, :name,
                    CAST(:imo AS int), :voyage, :load, :disc, :source)
                ON CONFLICT (shipment_id, leg_sequence) DO UPDATE SET
                    vessel_id = EXCLUDED.vessel_id, carrier_vessel_name = EXCLUDED.carrier_vessel_name,
                    carrier_vessel_imo = EXCLUDED.carrier_vessel_imo,
                    carrier_voyage_number = EXCLUDED.carrier_voyage_number,
                    load_unlocode = EXCLUDED.load_unlocode, discharge_unlocode = EXCLUDED.discharge_unlocode,
                    updated_at = now()
                """
            ),
            {
                "sid": shipment_id,
                "imo": leg.vessel_imo,
                "seq": leg.sequence,
                "name": leg.vessel_name,
                "voyage": leg.voyage_number,
                "load": leg.load_unlocode,
                "disc": leg.discharge_unlocode,
                "source": ship.source_id,
            },
        )
    await session.execute(
        sa.text("DELETE FROM shipment_vessels WHERE shipment_id = :sid AND leg_sequence > :n"),
        {"sid": shipment_id, "n": len(m.legs)},
    )


async def track(
    session: AsyncSession,
    registry: CarrierRegistry,
    *,
    carrier_code: str,
    reference: TrackingReference,
    threshold_hours: float,
) -> StoreOutcome:
    carrier_id, integration_id, status, reason = await _carrier(session, carrier_code)
    adapter = registry.get(carrier_code)
    if adapter is None:
        raise IntegrationNotAvailable(carrier_code, status, reason)
    await ensure_source(session, adapter)
    try:
        result: TrackingResult = await adapter.track(reference)
    except CarrierError as exc:
        await _log_calls(session, integration_id, exc.calls)
        if isinstance(exc, CarrierAuthError) and integration_id is not None:
            await session.execute(
                sa.text(
                    "UPDATE carrier_integrations SET status = 'authentication_required', "
                    "status_reason = :r, last_checked_at = now() WHERE id = :id"
                ),
                {"id": integration_id, "r": str(exc)},
            )
        await session.commit()  # keep the call logs even though tracking failed
        raise
    await _log_calls(session, integration_id, result.calls)
    if result.unmapped_codes:
        log.warning("carrier_unmapped_codes", carrier=carrier_code, codes=result.unmapped_codes)
    source_id = await _source_id(session, source_key_for(adapter))
    outcome = await store_result(
        session,
        org_id=await local_org_id(session),
        carrier_id=carrier_id,
        reference=reference,
        events=result.events,
        source_id=source_id,
        threshold_hours=threshold_hours,
    )
    now = utcnow()
    await session.execute(
        sa.text(
            "UPDATE data_sources SET status = 'active', last_success_at = :now, updated_at = now() "
            "WHERE id = :id"
        ),
        {"id": source_id, "now": now},
    )
    if integration_id is not None:
        await session.execute(
            sa.text(
                "UPDATE carrier_integrations SET last_success_at = :now, last_checked_at = :now "
                "WHERE id = :id"
            ),
            {"id": integration_id, "now": now},
        )
    return outcome
