"""Signed carrier push webhooks (DCSA Track & Trace events).

Signature scheme (HMAC-SHA256 with a per-carrier shared secret):

    X-ChainSight-Delivery:  <unique delivery id, <= 200 chars>
    X-ChainSight-Signature: t=<unix seconds>,v1=<hex hmac_sha256(secret, "<t>.<raw body>")>

- The timestamp must be within `webhook_tolerance_seconds` of the server clock, and it is part
  of the signed message, so a captured request cannot be replayed later.
- (carrier, delivery id) is unique in `webhook_deliveries`: a replay inside the tolerance
  window is acknowledged as a duplicate and ignored.
- Several `v1=` entries are accepted (secret rotation). Comparison is constant-time.

Events only update shipments that an organization already tracks with that carrier (matched
by booking, bill of lading or container reference). Unknown references are counted and
dropped: a push can never create shipments in anyone's organization.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from core.settings import Settings
from integrations.carriers.dcsa import normalize_dcsa_event
from integrations.carriers.models import NormalizedEvent, TrackingReference
from integrations.carriers.references import normalize_reference
from services.shipment_tracking.service import store_result

SIGNATURE_HEADER = "X-ChainSight-Signature"
DELIVERY_HEADER = "X-ChainSight-Delivery"
_DOC_TYPES = {"BKG": "booking", "TRD": "bill_of_lading"}


class WebhookRejected(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def secrets_for(settings: Settings, carrier_code: str) -> list[str]:
    if settings.carrier_webhook_secrets is None:
        return []
    try:
        raw = json.loads(settings.carrier_webhook_secrets.get_secret_value())
    except ValueError:
        return []
    value = raw.get(carrier_code) if isinstance(raw, dict) else None
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        return [v for v in value if isinstance(v, str) and v]
    return []


def sign(secret: str, timestamp: int, body: bytes) -> str:
    return hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()


def verify_signature(
    secrets: list[str], header: str | None, body: bytes, *, now: datetime, tolerance_s: int
) -> datetime:
    """Return the signed timestamp, or raise WebhookRejected."""
    if not header:
        raise WebhookRejected(401, "signature_missing", f"{SIGNATURE_HEADER} header is required")
    parts = [p.strip().split("=", 1) for p in header.split(",") if "=" in p]
    ts_values = [v for k, v in parts if k == "t"]
    sigs = [v for k, v in parts if k == "v1"]
    try:
        ts = int(ts_values[0])
    except (IndexError, ValueError) as exc:
        raise WebhookRejected(401, "signature_invalid", "Malformed signature header") from exc
    if abs(now.timestamp() - ts) > tolerance_s:
        raise WebhookRejected(401, "signature_expired", "Signature timestamp is outside tolerance")
    expected = [sign(s, ts, body) for s in secrets]
    if not any(hmac.compare_digest(e, s) for e in expected for s in sigs):
        raise WebhookRejected(401, "signature_invalid", "Signature does not match")
    return datetime.fromtimestamp(ts, tz=UTC)


def references_in(event: dict[str, Any]) -> set[tuple[str, str]]:
    refs: set[tuple[str, str]] = set()
    docs = event.get("documentReferences")
    for d in docs if isinstance(docs, list) else []:
        if isinstance(d, dict):
            kind = _DOC_TYPES.get(str(d.get("documentReferenceType", "")).upper())
            value = d.get("documentReferenceValue")
            if kind and value:
                refs.add((kind, normalize_reference(str(value))))
    if event.get("equipmentReference"):
        refs.add(("container", normalize_reference(str(event["equipmentReference"]))))
    return refs


@dataclass
class PushOutcome:
    status: str  # processed | duplicate
    delivery_id: str
    events_received: int = 0
    events_inserted: int = 0
    unmatched_events: int = 0
    skipped_events: int = 0
    shipments: list[uuid.UUID] = field(default_factory=list)
    organizations: set[uuid.UUID] = field(default_factory=set)


async def ingest_push(
    session: AsyncSession,
    *,
    carrier_code: str,
    delivery_id: str,
    signed_at: datetime,
    payload: Any,
    threshold_hours: float,
) -> PushOutcome:
    carrier_id = (
        await session.execute(
            sa.text("SELECT id FROM carriers WHERE code = :c"), {"c": carrier_code}
        )
    ).scalar_one_or_none()
    if carrier_id is None:
        raise WebhookRejected(404, "carrier_not_found", f"Unknown carrier '{carrier_code}'")
    raw_events = payload.get("events") if isinstance(payload, dict) else payload
    if not isinstance(raw_events, list):
        raise WebhookRejected(422, "invalid_payload", "Expected a JSON list of DCSA events")

    delivery = (
        await session.execute(
            sa.text(
                "INSERT INTO webhook_deliveries (carrier_id, delivery_id, signed_at, status, "
                "events_received) VALUES (:c, :d, :t, 'processing', :n) "
                "ON CONFLICT (carrier_id, delivery_id) DO NOTHING RETURNING id"
            ),
            {"c": carrier_id, "d": delivery_id, "t": signed_at, "n": len(raw_events)},
        )
    ).scalar_one_or_none()
    outcome = PushOutcome(
        status="processed", delivery_id=delivery_id, events_received=len(raw_events)
    )
    if delivery is None:
        outcome.status = "duplicate"
        return outcome

    # Group normalized events by the tracked shipments they reference.
    by_shipment: dict[uuid.UUID, list[NormalizedEvent]] = {}
    shipments: dict[uuid.UUID, Any] = {}
    for raw in raw_events:
        if not isinstance(raw, dict):
            outcome.skipped_events += 1
            continue
        event, _ = normalize_dcsa_event(raw)
        if event is None:
            outcome.skipped_events += 1
            continue
        refs = references_in(raw)
        matched: Sequence[Any] = []
        if refs:
            matched = (
                await session.execute(
                    sa.text(
                        "SELECT id, organization_id, reference_type, reference_value, source_id "
                        "FROM shipments WHERE carrier_id = :c AND (reference_type, reference_value) "
                        "IN (SELECT * FROM jsonb_to_recordset(CAST(:refs AS jsonb)) "
                        "AS r(t text, v text))"
                    ),
                    {"c": carrier_id, "refs": json.dumps([{"t": t, "v": v} for t, v in refs])},
                )
            ).all()
        if not matched:
            outcome.unmatched_events += 1
            continue
        for s in matched:
            shipments[s.id] = s
            by_shipment.setdefault(s.id, []).append(event)

    for sid, events in by_shipment.items():
        s = shipments[sid]
        stored = await store_result(
            session, org_id=s.organization_id, carrier_id=carrier_id,
            reference=TrackingReference(reference_type=s.reference_type, value=s.reference_value),
            events=events, source_id=s.source_id, threshold_hours=threshold_hours,
        )  # fmt: skip
        outcome.events_inserted += stored.events_inserted
        outcome.shipments.append(sid)
        outcome.organizations.add(s.organization_id)

    await session.execute(
        sa.text("UPDATE webhook_deliveries SET status = 'processed', error = :e WHERE id = :i"),
        {"i": delivery, "e": (f"{outcome.unmatched_events} unmatched, {outcome.skipped_events} skipped"
                              if outcome.unmatched_events or outcome.skipped_events else None)},
    )  # fmt: skip
    return outcome
