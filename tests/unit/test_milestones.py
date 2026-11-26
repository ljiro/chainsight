from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from database.enums import DelayStatus, ShipmentStatus
from services.shipment_tracking.milestones import derive_milestones, derive_route, derive_status

T0 = datetime(2026, 11, 1, tzinfo=UTC)


@dataclass(frozen=True)
class E:
    event_code: str
    classifier: str
    days: float
    loc: str
    created_days: float | None = None
    event_type: str = "equipment"
    vessel_name: str | None = None
    vessel_imo: int | None = None
    voyage_number: str | None = None

    @property
    def event_at(self) -> datetime:
        return T0 + timedelta(days=self.days)

    @property
    def event_created_at(self) -> datetime | None:
        return T0 + timedelta(days=self.created_days) if self.created_days is not None else None

    @property
    def location_unlocode(self) -> str:
        return self.loc


PLAN = [
    E("GTIN", "PLANNED", 1, "CNSHG", 0),
    E("LOAD", "PLANNED", 2, "CNSHG", 0, vessel_name="V1", voyage_number="1E"),
    E("DISC", "PLANNED", 10, "SGSIN", 0, vessel_name="V1", voyage_number="1E"),
    E("LOAD", "PLANNED", 12, "SGSIN", 0, vessel_name="V2", vessel_imo=9811000, voyage_number="2W"),
    E("ARRI", "PLANNED", 30, "NLRTM", 0, "transport", vessel_name="V2", voyage_number="2W"),
    E("DISC", "PLANNED", 30.5, "NLRTM", 0, vessel_name="V2", voyage_number="2W"),
]


def test_route_from_events() -> None:
    assert derive_route(PLAN) == ("CNSHG", "NLRTM")
    assert derive_route(PLAN, origin="XXAAA") == ("XXAAA", "NLRTM")


@pytest.mark.parametrize(
    ("actual", "status"),
    [
        ([], ShipmentStatus.PLANNED),
        ([E("GTIN", "ACTUAL", 1, "CNSHG")], ShipmentStatus.AT_ORIGIN),
        ([E("LOAD", "ACTUAL", 2, "CNSHG")], ShipmentStatus.LOADED),
        ([E("DEPA", "ACTUAL", 2.2, "CNSHG", event_type="transport")], ShipmentStatus.IN_TRANSIT),
        ([E("DISC", "ACTUAL", 10, "SGSIN")], ShipmentStatus.TRANSSHIPMENT),
        (
            [E("DISC", "ACTUAL", 10, "SGSIN"), E("LOAD", "ACTUAL", 12, "SGSIN")],
            ShipmentStatus.LOADED,
        ),
        ([E("DISC", "ACTUAL", 31, "NLRTM")], ShipmentStatus.DISCHARGED),
        (
            [E("DISC", "ACTUAL", 31, "NLRTM"), E("GTOT", "ACTUAL", 33, "NLRTM")],
            ShipmentStatus.DELIVERED,
        ),
        ([E("GTOT", "ACTUAL", 0.5, "CNSHG")], ShipmentStatus.PLANNED),  # empty pickup at origin
        (
            [
                E("LOAD", "ACTUAL", 2, "CNSHG"),
                E("ARRI", "ACTUAL", 30, "NLRTM", event_type="transport"),
            ],
            ShipmentStatus.IN_TRANSIT,
        ),
    ],
)
def test_status_progression(actual: list[E], status: ShipmentStatus) -> None:
    assert derive_status([*PLAN, *actual], "NLRTM") is status


def test_estimates_never_advance_status() -> None:
    assert (
        derive_status([*PLAN, E("DISC", "ESTIMATED", 31, "NLRTM")], "NLRTM")
        is ShipmentStatus.PLANNED
    )


def test_delay_uses_latest_estimate_against_original_plan() -> None:
    events = [
        *PLAN,
        E("ARRI", "ESTIMATED", 31, "NLRTM", 20, "transport"),
        E("ARRI", "ESTIMATED", 32.5, "NLRTM", 25, "transport"),  # issued later: wins
        E("ARRI", "ESTIMATED", 30.2, "NLRTM", 22, "transport"),  # older estimate: ignored
    ]
    m = derive_milestones(events)
    assert m.planned_arrival_at == T0 + timedelta(days=30)
    assert m.current_eta_at == T0 + timedelta(days=32.5)
    assert m.delay_hours == 60.0 and m.delay_status is DelayStatus.DELAYED


def test_replans_do_not_move_the_baseline() -> None:
    replan = E("ARRI", "PLANNED", 34, "NLRTM", 15, "transport")  # carrier re-plans later
    m = derive_milestones([*PLAN, replan])
    assert m.planned_arrival_at == T0 + timedelta(days=30)  # earliest-issued plan
    assert m.current_eta_at == T0 + timedelta(days=34)
    assert m.delay_status is DelayStatus.DELAYED


def test_stored_planned_arrival_is_authoritative() -> None:
    stored = T0 + timedelta(days=29)
    m = derive_milestones(PLAN, stored_planned_arrival=stored)
    assert m.planned_arrival_at == stored


@pytest.mark.parametrize(
    ("arrive_day", "expected"),
    [(30.3, DelayStatus.ARRIVED_ON_TIME), (31, DelayStatus.ARRIVED_LATE)],
)
def test_actual_arrival_threshold(arrive_day: float, expected: DelayStatus) -> None:
    m = derive_milestones(
        [*PLAN, E("ARRI", "ACTUAL", arrive_day, "NLRTM", event_type="transport")],
        threshold_hours=12,
    )
    assert m.delay_status is expected and m.actual_arrival_at == T0 + timedelta(days=arrive_day)


def test_disc_is_used_when_no_arrival_event_exists() -> None:
    plan = [e for e in PLAN if e.event_code != "ARRI"]
    m = derive_milestones(plan)
    assert m.planned_arrival_at == T0 + timedelta(days=30.5)


def test_unknown_without_plan() -> None:
    m = derive_milestones([E("LOAD", "ACTUAL", 2, "CNSHG")])
    assert m.delay_status is DelayStatus.UNKNOWN and m.delay_hours is None


def test_legs_follow_vessels_in_order() -> None:
    legs = derive_milestones(PLAN).legs
    assert [
        (
            leg.vessel_name,
            leg.voyage_number,
            leg.load_unlocode,
            leg.discharge_unlocode,
            leg.vessel_imo,
        )
        for leg in legs
    ] == [
        ("V1", "1E", "CNSHG", "SGSIN", None),
        ("V2", "2W", "SGSIN", "NLRTM", 9811000),
    ]
