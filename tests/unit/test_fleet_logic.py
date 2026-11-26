from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from database.enums import VesselClass
from services.fleet_tracking.detection import (
    GeofenceObservation,
    angle_diff_deg,
    detect_port_events,
    initial_bearing_deg,
    is_approaching,
)
from services.vessel_registry.classification import (
    guess_carrier_from_name,
    vessel_class_from_ship_type,
)
from services.vessel_registry.quality import QualityFlag, haversine_m, quality_flags

T0 = datetime(2026, 11, 26, tzinfo=UTC)
RTM, HAM = uuid.uuid4(), uuid.uuid4()


def obs(
    minutes: int, port: uuid.UUID | None, sog: float | None, nav: int | None = 0
) -> GeofenceObservation:
    return GeofenceObservation(T0 + timedelta(minutes=minutes), port, sog, nav)


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (None, VesselClass.UNKNOWN),
        (30, VesselClass.FISHING),
        (52, VesselClass.TUG_SERVICE),
        (37, VesselClass.PLEASURE),
        (60, VesselClass.PASSENGER),
        (70, VesselClass.CARGO),
        (79, VesselClass.CARGO),
        (84, VesselClass.TANKER),
        (90, VesselClass.OTHER),
        (5, VesselClass.UNKNOWN),
    ],
)
def test_vessel_class_from_ship_type(code: int | None, expected: VesselClass) -> None:
    assert vessel_class_from_ship_type(code) is expected


def test_ais_never_yields_container_or_lng() -> None:
    reserved = {
        VesselClass.CONTAINER,
        VesselClass.BULK,
        VesselClass.LNG_CARRIER,
        VesselClass.LPG_CARRIER,
    }
    assert {vessel_class_from_ship_type(c) for c in range(100)}.isdisjoint(reserved)


@pytest.mark.parametrize(
    ("name", "carrier"),
    [
        ("MAERSK KENSINGTON", "maersk"),
        ("MSC GULSUN", "msc"),
        ("CMA CGM JACQUES SAADE", "cma-cgm"),
        ("EVER GIVEN", "evergreen"),
        ("ONE INNOVATION", "one"),
        ("COSCO SHIPPING UNIVERSE", "cosco"),
        ("EVERGREEN STAR", None),  # 'EVER ' requires a space
        ("MSCOTT", None),
        ("SYNTHETIC AEX 01", None),
        (None, None),
    ],
)
def test_carrier_name_heuristic(name: str | None, carrier: str | None) -> None:
    guess = guess_carrier_from_name(name)
    assert (guess.carrier_code if guess else None) == carrier
    if guess:
        assert guess.confidence < 1  # a heuristic is never certain


def test_quality_flags_detect_jumps_and_ordering() -> None:
    prev = (T0, 51.9, 4.0)
    ok = quality_flags(observed_at=T0 + timedelta(minutes=1), lat=51.903, lon=4.0, sog=10, cog=90,
                       accuracy_high=True, previous=prev)  # fmt: skip
    assert ok == QualityFlag.NONE
    jump = quality_flags(observed_at=T0 + timedelta(minutes=1), lat=52.9, lon=4.0, sog=10, cog=90,
                         accuracy_high=True, previous=prev)  # fmt: skip
    assert QualityFlag.IMPLIED_SPEED_JUMP in jump
    late = quality_flags(observed_at=T0 - timedelta(minutes=1), lat=51.9, lon=4.0, sog=None,
                         cog=None, accuracy_high=False, previous=prev)  # fmt: skip
    assert late == (
        QualityFlag.OUT_OF_ORDER
        | QualityFlag.MISSING_SOG
        | QualityFlag.MISSING_COG
        | QualityFlag.LOW_ACCURACY
    )


def test_haversine_one_degree_latitude() -> None:
    assert haversine_m(0, 0, 1, 0) == pytest.approx(111_195, rel=1e-3)


def test_port_call_arrival_and_departure() -> None:
    events, open_port = detect_port_events(
        None,
        [
            obs(0, None, 14),
            obs(10, RTM, 6),  # entering at speed: not yet a call
            obs(20, RTM, 0.2),  # stopped inside: arrival
            obs(30, RTM, 0.0, nav=5),
            obs(40, RTM, 3),  # manoeuvring inside: still in port
            obs(50, None, 9),  # outside and moving: departure
        ],
    )
    assert [(e.kind, e.port_id, e.at) for e in events] == [
        ("arrival", RTM, T0 + timedelta(minutes=20)),
        ("departure", RTM, T0 + timedelta(minutes=50)),
    ]
    assert open_port is None


def test_transit_through_port_area_is_not_a_call() -> None:
    events, _ = detect_port_events(None, [obs(0, RTM, 12), obs(5, RTM, 11), obs(10, None, 12)])
    assert events == []


def test_stationary_jitter_outside_edge_keeps_call_open() -> None:
    events, open_port = detect_port_events(RTM, [obs(0, None, 0.1), obs(1, RTM, 0.0)])
    assert events == [] and open_port == RTM


def test_moving_directly_between_adjacent_ports() -> None:
    events, open_port = detect_port_events(RTM, [obs(0, HAM, 0.3)])
    assert [(e.kind, e.port_id) for e in events] == [("departure", RTM), ("arrival", HAM)]
    assert open_port == HAM


def test_events_are_time_ordered_even_if_input_is_not() -> None:
    events, _ = detect_port_events(None, [obs(10, None, 12), obs(0, RTM, 0)])
    assert [e.kind for e in events] == ["arrival", "departure"]


def test_bearing_and_angle_helpers() -> None:
    assert initial_bearing_deg(0, 0, 1, 0) == pytest.approx(0)
    assert initial_bearing_deg(0, 0, 0, 1) == pytest.approx(90)
    assert angle_diff_deg(350, 10) == pytest.approx(20)


def test_is_approaching() -> None:
    kw = {"vessel_lat": 51.5, "vessel_lon": 3.0, "port_lat": 51.92, "port_lon": 4.5}
    bearing = initial_bearing_deg(51.5, 3.0, 51.92, 4.5)
    assert is_approaching(cog_deg=bearing + 10, sog_knots=12, **kw)
    assert not is_approaching(cog_deg=bearing + 120, sog_knots=12, **kw)
    assert not is_approaching(cog_deg=bearing, sog_knots=1, **kw)
    assert not is_approaching(cog_deg=None, sog_knots=12, **kw)
