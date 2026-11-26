"""Pure milestone logic: status, route, original-plan delay, ETA and vessel legs.

Rules
- Status is derived from ACTUAL events only. Plans and estimates never advance it.
- The planned arrival is the destination arrival from the earliest-issued PLANNED event.
  Persistence stores it write-once, so re-plans cannot move the baseline.
- Current ETA = actual arrival if known, else the most recently issued ESTIMATE, else the
  most recently issued PLAN. Delay = current ETA - original planned arrival.
- Arrival = transport ARRI at the destination; if a carrier sends none, equipment DISC at
  the destination is used.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from database.enums import DelayStatus, ShipmentStatus


class EventLike(Protocol):
    @property
    def event_type(self) -> str: ...
    @property
    def event_code(self) -> str: ...
    @property
    def classifier(self) -> str: ...
    @property
    def event_at(self) -> datetime: ...
    @property
    def event_created_at(self) -> datetime | None: ...
    @property
    def location_unlocode(self) -> str | None: ...
    @property
    def vessel_name(self) -> str | None: ...
    @property
    def vessel_imo(self) -> int | None: ...
    @property
    def voyage_number(self) -> str | None: ...


@dataclass(frozen=True)
class Leg:
    sequence: int
    vessel_name: str | None
    vessel_imo: int | None
    voyage_number: str | None
    load_unlocode: str | None
    discharge_unlocode: str | None


@dataclass(frozen=True)
class Milestones:
    origin: str | None
    destination: str | None
    status: ShipmentStatus
    planned_arrival_at: datetime | None
    current_eta_at: datetime | None
    actual_arrival_at: datetime | None
    delay_hours: float | None
    delay_status: DelayStatus
    last_event_at: datetime | None
    legs: tuple[Leg, ...]


def _issued(e: EventLike) -> datetime:
    return e.event_created_at or e.event_at


def derive_route(
    events: Sequence[EventLike], origin: str | None = None, destination: str | None = None
) -> tuple[str | None, str | None]:
    by_time = sorted((e for e in events if e.location_unlocode), key=lambda e: e.event_at)
    if origin is None:
        first = next((e for e in by_time if e.event_code in ("GTIN", "STUF", "LOAD", "DEPA")), None)
        origin = first.location_unlocode if first else None
    if destination is None:
        last = next(
            (e for e in reversed(by_time) if e.event_code in ("ARRI", "DISC", "GTOT")), None
        )
        destination = last.location_unlocode if last else None
    return origin, destination


def derive_status(events: Sequence[EventLike], destination: str | None) -> ShipmentStatus:
    actuals = sorted((e for e in events if e.classifier == "ACTUAL"), key=lambda e: e.event_at)
    status = ShipmentStatus.PLANNED if events else ShipmentStatus.UNKNOWN
    for e in actuals:
        at_dest = destination is not None and e.location_unlocode == destination
        match e.event_code:
            case "GTIN" | "STUF" | "RECE":
                if status in (ShipmentStatus.PLANNED, ShipmentStatus.UNKNOWN):
                    status = ShipmentStatus.AT_ORIGIN
            case "LOAD":
                status = ShipmentStatus.LOADED
            case "DEPA":
                status = ShipmentStatus.IN_TRANSIT
            case "ARRI":
                # A recorded vessel arrival means the cargo has at least left origin.
                if status in (
                    ShipmentStatus.PLANNED,
                    ShipmentStatus.UNKNOWN,
                    ShipmentStatus.AT_ORIGIN,
                    ShipmentStatus.LOADED,
                ):
                    status = ShipmentStatus.IN_TRANSIT
            case "DISC":
                status = ShipmentStatus.DISCHARGED if at_dest else ShipmentStatus.TRANSSHIPMENT
            case "GTOT" | "STRP" if at_dest:
                status = ShipmentStatus.DELIVERED
            case _:
                pass
    return status


def _arrivals(events: Sequence[EventLike], destination: str | None) -> list[EventLike]:
    if destination is None:
        return []
    at_dest = [e for e in events if e.location_unlocode == destination]
    arri = [e for e in at_dest if e.event_code == "ARRI"]
    return arri or [e for e in at_dest if e.event_code == "DISC"]


def derive_legs(events: Sequence[EventLike]) -> tuple[Leg, ...]:
    legs: list[dict[str, object]] = []
    index: dict[tuple[str | None, str | None], dict[str, object]] = {}
    for e in sorted(events, key=lambda x: x.event_at):
        if not e.vessel_name and not e.vessel_imo:
            continue
        key = (e.vessel_name, e.voyage_number)
        leg = index.get(key)
        if leg is None:
            leg = {"vessel_name": e.vessel_name, "vessel_imo": e.vessel_imo,
                   "voyage_number": e.voyage_number, "load": None, "discharge": None}  # fmt: skip
            index[key] = leg
            legs.append(leg)
        if e.vessel_imo and not leg["vessel_imo"]:
            leg["vessel_imo"] = e.vessel_imo
        if e.event_code in ("LOAD", "DEPA") and leg["load"] is None:
            leg["load"] = e.location_unlocode
        if e.event_code in ("DISC", "ARRI"):
            leg["discharge"] = e.location_unlocode
    return tuple(
        Leg(i + 1, leg["vessel_name"], leg["vessel_imo"], leg["voyage_number"],  # type: ignore[arg-type]
            leg["load"], leg["discharge"])  # type: ignore[arg-type]
        for i, leg in enumerate(legs)
    )  # fmt: skip


def derive_milestones(
    events: Sequence[EventLike],
    *,
    origin: str | None = None,
    destination: str | None = None,
    stored_planned_arrival: datetime | None = None,
    threshold_hours: float = 12.0,
) -> Milestones:
    origin, destination = derive_route(events, origin, destination)
    arrivals = _arrivals(events, destination)
    planned = sorted((e for e in arrivals if e.classifier == "PLANNED"), key=_issued)
    estimated = sorted((e for e in arrivals if e.classifier == "ESTIMATED"), key=_issued)
    actual = sorted((e for e in arrivals if e.classifier == "ACTUAL"), key=lambda e: e.event_at)

    planned_arrival = stored_planned_arrival or (planned[0].event_at if planned else None)
    actual_arrival = actual[-1].event_at if actual else None
    current = actual_arrival or (estimated[-1].event_at if estimated else None)
    if current is None and planned:
        current = planned[-1].event_at  # latest re-plan

    delay_hours: float | None = None
    delay_status = DelayStatus.UNKNOWN
    if planned_arrival is not None and current is not None:
        delay_hours = round((current - planned_arrival).total_seconds() / 3600, 2)
        late = delay_hours > threshold_hours
        if actual_arrival is not None:
            delay_status = DelayStatus.ARRIVED_LATE if late else DelayStatus.ARRIVED_ON_TIME
        else:
            delay_status = DelayStatus.DELAYED if late else DelayStatus.ON_TIME

    return Milestones(
        origin=origin,
        destination=destination,
        status=derive_status(events, destination),
        planned_arrival_at=planned_arrival,
        current_eta_at=current,
        actual_arrival_at=actual_arrival,
        delay_hours=delay_hours,
        delay_status=delay_status,
        last_event_at=max((e.event_at for e in events if e.classifier == "ACTUAL"), default=None),
        legs=derive_legs(events),
    )
