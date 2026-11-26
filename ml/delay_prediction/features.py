"""Point-in-time delay features from a shipment's milestone events (pure).

An event can only be used at time `as_of` if it had been RECEIVED by then
(`available_at <= as_of`). Filtering on event time alone would leak information: a carrier
can publish a revised estimate days after the plan it revises.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime

FEATURES = [
    "planned_transit_days",
    "progress_fraction",
    "days_since_first_event",
    "n_vessels",
    "has_transshipment",
    "latest_estimate_delta_h",
    "has_estimate",
    "replan_delta_h",
    "departure_delay_h",
    "has_departed",
    "actual_events",
]


@dataclass(frozen=True)
class EventRecord:
    event_code: str
    classifier: str
    event_at: datetime
    available_at: datetime  # when ChainSight received it
    location: str | None
    vessel_name: str | None


@dataclass(frozen=True)
class DelayFeatures:
    planned_transit_days: float
    progress_fraction: float
    days_since_first_event: float
    n_vessels: int
    has_transshipment: int
    latest_estimate_delta_h: float
    has_estimate: int
    replan_delta_h: float
    departure_delay_h: float
    has_departed: int
    actual_events: int

    def as_dict(self) -> dict[str, float | int]:
        return asdict(self)


def _h(a: datetime, b: datetime) -> float:
    return (a - b).total_seconds() / 3600


def original_planned_arrival(events: list[EventRecord], destination: str) -> datetime | None:
    plans = [
        e
        for e in events
        if e.classifier == "PLANNED"
        and e.location == destination
        and e.event_code in ("ARRI", "DISC")
    ]
    arri = [e for e in plans if e.event_code == "ARRI"] or plans
    return min(arri, key=lambda e: e.available_at).event_at if arri else None


def delay_features(
    events: list[EventRecord], *, as_of: datetime, origin: str | None, destination: str
) -> DelayFeatures | None:
    """Features at `as_of`, or None if the shipment has no plan to compare against yet."""
    known = [e for e in events if e.available_at <= as_of]
    plan_arrival = original_planned_arrival(known, destination)
    if plan_arrival is None:
        return None
    plans = [e for e in known if e.classifier == "PLANNED"]
    plan_depart = min(
        (e.event_at for e in plans if e.event_code in ("DEPA", "LOAD") and e.location == origin),
        default=min(e.event_at for e in plans),
    )
    transit = max(_h(plan_arrival, plan_depart), 1.0)
    first_seen = min(e.available_at for e in known)

    def latest(code: str, classifier: str, location: str | None) -> EventRecord | None:
        cands = [
            e
            for e in known
            if e.event_code == code
            and e.classifier == classifier
            and (location is None or e.location == location)
        ]
        return max(cands, key=lambda e: e.available_at) if cands else None

    est = latest("ARRI", "ESTIMATED", destination)
    replan = latest("ARRI", "PLANNED", destination)
    act_dep = latest("DEPA", "ACTUAL", origin) or latest("LOAD", "ACTUAL", origin)
    plan_dep = next(
        (e for e in plans if e.event_code in ("DEPA", "LOAD") and e.location == origin), None
    )
    vessels = {e.vessel_name for e in plans if e.vessel_name}
    return DelayFeatures(
        planned_transit_days=round(transit / 24, 3),
        progress_fraction=round(min(max(_h(as_of, plan_depart) / transit, 0.0), 1.5), 4),
        days_since_first_event=round(_h(as_of, first_seen) / 24, 3),
        n_vessels=len(vessels),
        has_transshipment=int(len(vessels) > 1),
        latest_estimate_delta_h=round(_h(est.event_at, plan_arrival), 2) if est else 0.0,
        has_estimate=int(est is not None),
        replan_delta_h=round(_h(replan.event_at, plan_arrival), 2) if replan else 0.0,
        departure_delay_h=round(_h(act_dep.event_at, plan_dep.event_at), 2)
        if act_dep and plan_dep
        else 0.0,
        has_departed=int(act_dep is not None),
        actual_events=sum(1 for e in known if e.classifier == "ACTUAL"),
    )


def risk_factors(f: DelayFeatures) -> list[dict[str, object]]:
    """Human-readable drivers, derived from the features (not model internals)."""
    out: list[dict[str, object]] = []
    if f.has_estimate and f.latest_estimate_delta_h > 0:
        out.append(
            {
                "factor": "latest_estimate_behind_plan",
                "value_hours": f.latest_estimate_delta_h,
                "text": (
                    f"Carrier's latest estimate is {f.latest_estimate_delta_h:.0f} h behind "
                    "the original plan"
                ),
            }
        )
    if f.replan_delta_h > 0:
        out.append(
            {
                "factor": "replanned_later",
                "value_hours": f.replan_delta_h,
                "text": f"Arrival re-planned {f.replan_delta_h:.0f} h later than first plan",
            }
        )
    if f.departure_delay_h > 6:
        out.append(
            {
                "factor": "late_departure",
                "value_hours": f.departure_delay_h,
                "text": f"Departed {f.departure_delay_h:.0f} h after plan",
            }
        )
    if f.has_transshipment:
        out.append({"factor": "transshipment", "text": "Route includes a transshipment"})
    return out
