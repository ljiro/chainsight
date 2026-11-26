"""Demo carrier (synthetic). Produces DCSA-format events so the real normalization path runs.

Safeguards against passing synthetic data off as real:
- Only answers references that are obviously demo: bookings/B/Ls starting with "DEMO" and
  container numbers with the "ZZZU" owner prefix. Any real reference gets ShipmentNotFound.
  The demo never invents tracking data for a real booking.
- Vessel names start with "SYNTHETIC" and carry no IMO, so they never link to real AIS
  vessels.
- Rows are stored under the `carrier-demo` source (data_mode = synthetic).

Journeys are a deterministic function of the reference and the current time: planned
events are fixed at booking, actuals appear as time passes, and estimates are re-issued
(as new events) when they change.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, ClassVar

from core.timeutils import utcnow
from integrations.carriers.base import CarrierAdapter
from integrations.carriers.dcsa import normalize_dcsa_payload
from integrations.carriers.models import (
    IntegrationHealth,
    ProviderResponse,
    ShipmentNotFound,
    TrackingReference,
    TrackingResult,
)
from integrations.carriers.references import iso6346_check_digit, normalize_reference

# origin, transshipment (or None), destination, days to transshipment, total sea days
ROUTES: list[tuple[str, str | None, str, int, int]] = [
    ("CNSHG", "SGSIN", "NLRTM", 5, 28),
    ("KRPUS", None, "USLGB", 0, 13),
    ("SGSIN", None, "HKHKG", 0, 5),
    # Manila has no coordinates in UN/LOCODE: exercises "destination not mapped" handling.
    ("CNSHG", None, "PHMNL", 0, 6),
    ("NLRTM", None, "USNYC", 0, 10),
]
DELAY_PROFILES_H = [0, 0, 10, 36, 80]


@dataclass(frozen=True)
class DemoJourney:
    reference: str
    container: str
    origin: str
    transship: str | None
    destination: str
    booked_at: datetime
    delay_h: int
    leg1_days: int
    total_days: int


def _digest(reference: str) -> bytes:
    return hashlib.sha256(reference.encode()).digest()


def demo_container_for(reference: str) -> str:
    serial = f"{int.from_bytes(_digest(reference)[:4], 'big') % 1_000_000:06d}"
    first_ten = f"ZZZU{serial}"
    return f"{first_ten}{iso6346_check_digit(first_ten)}"


def is_demo_reference(ref: TrackingReference) -> bool:
    value = normalize_reference(ref.value)
    if ref.reference_type == "container":
        return value.startswith("ZZZU")
    return value.startswith("DEMO")


def journey_for(reference: str, now: datetime) -> DemoJourney:
    d = _digest(reference)
    origin, transship, destination, leg1, total = ROUTES[d[0] % len(ROUTES)]
    # Booked between 2 and 45 days ago (at a fixed hour) so journeys span every stage.
    booked = (now - timedelta(days=2 + d[1] % 44)).replace(minute=0, second=0, microsecond=0)
    booked = booked.replace(hour=d[2] % 24)
    return DemoJourney(
        reference=reference,
        container=reference if reference.startswith("ZZZU") else demo_container_for(reference),
        origin=origin,
        transship=transship,
        destination=destination,
        booked_at=booked,
        delay_h=DELAY_PROFILES_H[d[3] % len(DELAY_PROFILES_H)],
        leg1_days=leg1,
        total_days=total,
    )


def _event(kind: str, code: str, classifier: str, at: datetime, created: datetime, loc: str,
           j: DemoJourney, vessel: str | None = None, voyage: str | None = None,
           event_id: str | None = None) -> dict[str, Any]:  # fmt: skip
    ev: dict[str, Any] = {
        "eventID": event_id or f"{j.reference}-{classifier}-{code}-{loc}",
        "eventType": kind,
        "eventClassifierCode": classifier,
        "eventDateTime": at.isoformat(),
        "eventCreatedDateTime": created.isoformat(),
        "eventLocation": {"UNLocationCode": loc},
    }
    if kind == "EQUIPMENT":
        ev["equipmentEventTypeCode"] = code
        ev["equipmentReference"] = j.container
    else:
        ev["transportEventTypeCode"] = code
    if vessel:
        ev["transportCall"] = {
            "UNLocationCode": loc,
            "modeOfTransport": "VESSEL",
            "vessel": {"vesselName": vessel},  # no IMO: synthetic vessels never match real AIS
            "carrierExportVoyageNumber": voyage,
        }
    return ev


def build_events(j: DemoJourney, now: datetime) -> list[dict[str, Any]]:
    """Planned schedule + actuals up to `now` + current estimate for destination arrival."""
    t0, delay = j.booked_at, timedelta(hours=j.delay_h)
    v1, v2 = "SYNTHETIC DEMO EXPRESS", "SYNTHETIC DEMO FEEDER"
    voy1 = f"DM{int.from_bytes(_digest(j.reference)[4:6], 'big') % 900 + 100}E"
    voy2 = voy1.replace("E", "W")
    depart = t0 + timedelta(days=2, hours=4)
    # (kind, code, location, planned time, vessel, voyage, delayed?)
    plan: list[tuple[str, str, str, datetime, str | None, str | None, bool]] = [
        ("EQUIPMENT", "GTIN", j.origin, t0 + timedelta(days=1), None, None, False),
        ("EQUIPMENT", "LOAD", j.origin, t0 + timedelta(days=2), v1, voy1, False),
        ("TRANSPORT", "DEPA", j.origin, depart, v1, voy1, False),
    ]
    if j.transship:
        at_ts = depart + timedelta(days=j.leg1_days)
        plan += [
            ("TRANSPORT", "ARRI", j.transship, at_ts, v1, voy1, True),
            ("EQUIPMENT", "DISC", j.transship, at_ts + timedelta(hours=6), v1, voy1, True),
            ("EQUIPMENT", "LOAD", j.transship, at_ts + timedelta(days=2), v2, voy2, True),
            ("TRANSPORT", "DEPA", j.transship, at_ts + timedelta(days=2, hours=4), v2, voy2, True),
        ]
    final_vessel, final_voy = (v2, voy2) if j.transship else (v1, voy1)
    arrive = depart + timedelta(days=j.total_days)
    plan += [
        ("TRANSPORT", "ARRI", j.destination, arrive, final_vessel, final_voy, True),
        (
            "EQUIPMENT",
            "DISC",
            j.destination,
            arrive + timedelta(hours=8),
            final_vessel,
            final_voy,
            True,
        ),
        ("EQUIPMENT", "GTOT", j.destination, arrive + timedelta(days=2), None, None, True),
    ]

    events: list[dict[str, Any]] = []
    for kind, code, loc, planned_at, vessel, voyage, delayed in plan:
        events.append(_event(kind, code, "PLN", planned_at, t0, loc, j, vessel, voyage))
        actual_at = planned_at + (delay if delayed else timedelta())
        if actual_at <= now:
            events.append(_event(kind, code, "ACT", actual_at, actual_at, loc, j, vessel, voyage))

    arrival_actual = arrive + delay
    if arrival_actual > now and depart <= now:
        # Delays emerge during the voyage: the estimate reveals half the eventual delay at
        # departure and all of it by arrival. Each changed estimate is a new event (append-
        # only history); an unchanged estimate re-issued later dedupes by its id.
        progress = min(1.0, (now - depart) / (arrival_actual - depart))
        estimate = arrive + timedelta(hours=round(j.delay_h * (0.5 + 0.5 * progress)))
        issued = now.replace(minute=0, second=0, microsecond=0)
        events.append(
            _event("TRANSPORT", "ARRI", "EST", estimate, issued, j.destination, j,
                   final_vessel, final_voy,
                   event_id=f"{j.reference}-EST-ARRI-{j.destination}-{estimate:%Y%m%d%H}")
        )  # fmt: skip
    return events


class DemoCarrierAdapter(CarrierAdapter):
    carrier_code: ClassVar[str] = "demo"
    adapter_key: ClassVar[str] = "demo"
    data_mode: ClassVar[str] = "synthetic"

    def __init__(self, clock: Any = utcnow) -> None:
        self._clock = clock

    async def track_shipment(self, reference: TrackingReference) -> ProviderResponse:
        if not is_demo_reference(reference):
            raise ShipmentNotFound(
                "The demo carrier only answers demo references (bookings starting 'DEMO', "
                "containers starting 'ZZZU'). It never generates data for real references."
            )
        now = self._clock()
        value = normalize_reference(reference.value)
        return ProviderResponse(
            reference=reference, payload=build_events(journey_for(value, now), now), fetched_at=now
        )

    async def normalize_events(self, response: ProviderResponse) -> TrackingResult:
        events, unmapped, skipped = normalize_dcsa_payload(response.payload)
        return TrackingResult(
            reference=response.reference, events=events, fetched_at=response.fetched_at,
            unmapped_codes=unmapped, skipped_events=skipped,
        )  # fmt: skip

    async def health_check(self) -> IntegrationHealth:
        return IntegrationHealth(
            status="demo_data_only",
            reason="Synthetic shipments generated by the demo adapter for development.",
            checked_at=self._clock(),
        )
