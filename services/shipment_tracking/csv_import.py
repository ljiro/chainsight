"""CSV shipment-event import (data source `csv-import`, data mode historical).

One row per event:

    carrier_code,reference_type,reference_value,event_code,classifier,event_at,
    location_unlocode,container_number,vessel_name,vessel_imo,voyage_number,
    origin_unlocode,destination_unlocode,description

Required: carrier_code, reference_type, reference_value, event_code, classifier, event_at
(ISO 8601 with a timezone offset). Validation is all-or-nothing: if any row is invalid,
nothing is imported and every problem is reported with its row number.
"""

from __future__ import annotations

import csv
import io
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from core.timeutils import ensure_utc, utcnow
from integrations.ais.messages import is_valid_imo
from integrations.carriers.dcsa import (
    DESCRIPTIONS,
    EQUIPMENT_CODES,
    SHIPMENT_CODES,
    TRANSPORT_CODES,
)
from integrations.carriers.models import NormalizedEvent, TrackingReference
from integrations.carriers.references import is_valid_container_number, normalize_reference
from services.ingestion.sources import write_log
from services.shipment_tracking.service import local_org_id, store_result

REQUIRED = (
    "carrier_code",
    "reference_type",
    "reference_value",
    "event_code",
    "classifier",
    "event_at",
)
CLASSIFIER_ALIASES = {"PLN": "PLANNED", "EST": "ESTIMATED", "ACT": "ACTUAL",
                      "PLANNED": "PLANNED", "ESTIMATED": "ESTIMATED", "ACTUAL": "ACTUAL"}  # fmt: skip
REFERENCE_ALIASES = {"container": "container", "booking": "booking", "bl": "bill_of_lading",
                     "bill_of_lading": "bill_of_lading"}  # fmt: skip
MAX_ROWS = 50_000


@dataclass(frozen=True)
class RowError:
    row: int
    field: str
    message: str


@dataclass
class ImportReport:
    rows: int = 0
    shipments_created: int = 0
    shipments_updated: int = 0
    events_inserted: int = 0
    events_duplicate: int = 0
    errors: list[RowError] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class _Row:
    carrier_code: str
    reference: TrackingReference
    event: NormalizedEvent
    origin: str | None
    destination: str | None


def _unlocode(value: str | None) -> str | None:
    v = (value or "").strip().upper()
    return v or None


def parse_rows(text: str, known_carriers: set[str]) -> tuple[list[_Row], list[RowError]]:
    reader = csv.DictReader(io.StringIO(text))
    missing = [c for c in REQUIRED if c not in (reader.fieldnames or [])]
    if missing:
        return [], [RowError(1, ",".join(missing), "missing required column(s)")]
    rows: list[_Row] = []
    errors: list[RowError] = []
    for n, raw in enumerate(reader, start=2):  # row 1 is the header
        if n - 1 > MAX_ROWS:
            errors.append(RowError(n, "*", f"file exceeds {MAX_ROWS} rows"))
            break
        r = {k: (v or "").strip() for k, v in raw.items() if k}

        def bad(field_name: str, message: str, row_no: int = n) -> None:
            errors.append(RowError(row_no, field_name, message))

        before = len(errors)
        for col in REQUIRED:
            if not r.get(col):
                bad(col, "required")
        if len(errors) > before:
            continue
        carrier = r["carrier_code"].lower()
        if carrier not in known_carriers:
            bad("carrier_code", f"unknown carrier '{r['carrier_code']}'")
        rtype = REFERENCE_ALIASES.get(r["reference_type"].lower())
        if rtype is None:
            bad("reference_type", "must be container, booking or bill_of_lading")
        code = r["event_code"].upper()
        if code in EQUIPMENT_CODES:
            etype = "equipment"
        elif code in TRANSPORT_CODES:
            etype = "transport"
        elif code in SHIPMENT_CODES:
            etype = "shipment"
        else:
            bad("event_code", f"unknown DCSA event code '{code}'")
        classifier = CLASSIFIER_ALIASES.get(r["classifier"].upper())
        if classifier is None:
            bad("classifier", "must be PLANNED, ESTIMATED or ACTUAL (or PLN/EST/ACT)")
        try:
            event_at = ensure_utc(datetime.fromisoformat(r["event_at"].replace("Z", "+00:00")))
        except ValueError:
            bad("event_at", "must be ISO 8601 with a timezone offset, e.g. 2026-11-20T08:00:00Z")
        for col in ("location_unlocode", "origin_unlocode", "destination_unlocode"):
            v = _unlocode(r.get(col))
            if v and (len(v) != 5 or not v[:2].isalpha()):
                bad(col, "must be a 5-character UN/LOCODE")
        container = (
            normalize_reference(r["container_number"]) if r.get("container_number") else None
        )
        if container and not is_valid_container_number(container):
            bad("container_number", f"'{container}' fails the ISO 6346 check digit")
        ref_value = normalize_reference(r["reference_value"])
        if rtype == "container" and not is_valid_container_number(ref_value):
            bad("reference_value", f"'{ref_value}' fails the ISO 6346 check digit")
        imo: int | None = None
        if r.get("vessel_imo"):
            try:
                imo = int(r["vessel_imo"])
            except ValueError:
                bad("vessel_imo", "must be a 7-digit IMO number")
            else:
                if not is_valid_imo(imo):
                    bad("vessel_imo", f"{imo} fails the IMO checksum")
        if len(errors) > before:
            continue
        rows.append(
            _Row(
                carrier_code=carrier,
                reference=TrackingReference(reference_type=rtype, value=ref_value),
                event=NormalizedEvent(
                    event_type=etype,
                    event_code=code,
                    classifier=classifier,
                    event_at=event_at,
                    location_unlocode=_unlocode(r.get("location_unlocode")),
                    container_number=container,
                    vessel_imo=imo,
                    vessel_name=r.get("vessel_name") or None,
                    voyage_number=r.get("voyage_number") or None,
                    description=r.get("description") or DESCRIPTIONS.get(code),
                    raw_code=code,
                ),
                origin=_unlocode(r.get("origin_unlocode")),
                destination=_unlocode(r.get("destination_unlocode")),
            )
        )
    return rows, errors


async def import_csv(session: AsyncSession, text: str, *, threshold_hours: float) -> ImportReport:
    started = utcnow()
    carriers = {
        r.code: r.id for r in await session.execute(sa.text("SELECT id, code FROM carriers"))
    }
    rows, errors = parse_rows(text, set(carriers))
    report = ImportReport(rows=len(rows) + len({e.row for e in errors}), errors=errors)
    source_id = (
        await session.execute(sa.text("SELECT id FROM data_sources WHERE key = 'csv-import'"))
    ).scalar_one()
    if errors or not rows:
        if not rows and not errors:
            report.errors.append(RowError(1, "*", "no data rows"))
        await write_log(session, source_id, started, status="failed", received=report.rows,
                        rejected=len({e.row for e in report.errors}),
                        details={"errors": [e.__dict__ for e in report.errors[:50]]})  # fmt: skip
        return report

    groups: dict[tuple[str, str, str], list[_Row]] = defaultdict(list)
    for row in rows:
        groups[(row.carrier_code, row.reference.reference_type, row.reference.value)].append(row)
    org_id = await local_org_id(session)
    for (carrier, _, _), group in groups.items():
        origin = next((g.origin for g in group if g.origin), None)
        destination = next((g.destination for g in group if g.destination), None)
        outcome = await store_result(
            session,
            org_id=org_id,
            carrier_id=carriers[carrier],
            reference=group[0].reference,
            events=[g.event for g in group],
            source_id=source_id,
            threshold_hours=threshold_hours,
            origin=origin,
            destination=destination,
        )
        if outcome.created:
            report.shipments_created += 1
        else:
            report.shipments_updated += 1
        report.events_inserted += outcome.events_inserted
        report.events_duplicate += outcome.events_duplicate

    details: dict[str, Any] = {"shipments_created": report.shipments_created,
                               "shipments_updated": report.shipments_updated}  # fmt: skip
    await write_log(session, source_id, started, status="succeeded", received=report.rows,
                    accepted=report.events_inserted, duplicate=report.events_duplicate,
                    details=details)  # fmt: skip
    await session.execute(
        sa.text(
            "UPDATE data_sources SET status = 'active', last_success_at = now() WHERE id = :id"
        ),
        {"id": source_id},
    )
    return report
