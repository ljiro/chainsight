from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ml.anomaly_detection.rules import (
    Fix,
    RuleConfig,
    ais_silence,
    haversine_nm,
    long_port_wait,
    route_deviation,
    speed_drop,
    unexpected_stop,
)
from ml.common.destinations import resolve_destination

T0 = datetime(2026, 11, 26, 12, tzinfo=UTC)
CFG = RuleConfig()


def fx(minutes: float, sog: float | None, nav: int | None = 0, in_port: bool = False,
       lat: float = 10.0, lon: float = 120.0) -> Fix:  # fmt: skip
    return Fix(T0 + timedelta(minutes=minutes), lat, lon, sog, nav, in_port)


def test_unexpected_stop_requires_duration_at_sea_and_underway_status() -> None:
    stopped = [fx(m, 0.2) for m in range(0, 70, 10)]
    a = unexpected_stop([fx(-10, 12), *stopped], CFG)
    assert a and a.type == "unexpected_stop" and a.evidence["minutes"] == 60
    assert unexpected_stop([fx(m, 0.2) for m in range(0, 50, 10)], CFG) is None  # too short
    assert unexpected_stop([fx(m, 0.2, nav=1) for m in range(0, 70, 10)], CFG) is None  # anchored
    assert unexpected_stop([fx(m, 0.2, in_port=True) for m in range(0, 70, 10)], CFG) is None
    assert unexpected_stop([*stopped, fx(80, 9)], CFG) is None  # moving again now


def test_speed_drop() -> None:
    fixes = [fx(m, 15) for m in (0, 10, 20)] + [fx(m, 4) for m in (35, 40, 45)]
    a = speed_drop(fixes, CFG)
    assert a and a.evidence == {"before_kn": 15, "after_kn": 4}
    assert speed_drop([fx(m, 15) for m in (0, 10, 20, 35, 40, 45)], CFG) is None
    assert (
        speed_drop([fx(m, 6) for m in (0, 10, 20)] + [fx(m, 1) for m in (35, 40, 45)], CFG) is None
    )  # slow already


def test_ais_silence_only_for_moving_vessels_within_window() -> None:
    last = fx(0, 12)
    assert ais_silence(last, T0 + timedelta(hours=3), CFG).type == "ais_silence"  # type: ignore[union-attr]
    assert ais_silence(last, T0 + timedelta(hours=1), CFG) is None
    assert (
        ais_silence(last, T0 + timedelta(hours=72), CFG) is None
    )  # long gone: not "recent" silence
    assert ais_silence(fx(0, 0.1), T0 + timedelta(hours=3), CFG) is None
    assert ais_silence(fx(0, 12, in_port=True), T0 + timedelta(hours=3), CFG) is None


def test_long_port_wait_default_and_port_median() -> None:
    now = T0 + timedelta(hours=80)
    assert long_port_wait(T0, now, None, CFG).evidence["threshold_hours"] == 72  # type: ignore[union-attr]
    assert long_port_wait(T0, now, 30.0, CFG) is None  # 3x median = 90 h
    assert long_port_wait(None, now, None, CFG) is None  # unknown arrival: no claim


def test_route_deviation_moving_away_from_destination() -> None:
    dest = (1.28, 103.85)  # Singapore
    away = [fx(m, 14, lat=10 + m / 60, lon=110) for m in (0, 20, 40, 60)]  # heading north, away
    a = route_deviation(away, dest, CFG)
    assert a and a.evidence["distance_end_nm"] > a.evidence["distance_start_nm"]
    toward = [fx(m, 14, lat=10 - m / 60, lon=110) for m in (0, 20, 40, 60)]
    assert route_deviation(toward, dest, CFG) is None
    near = [fx(m, 14, lat=1.3 + m / 600, lon=103.9) for m in (0, 20, 40, 60)]
    assert route_deviation(near, dest, CFG) is None  # too close: coastal routing bends
    assert route_deviation(away, None, CFG) is None


def test_haversine_nm() -> None:
    assert abs(haversine_nm(0, 0, 1, 0) - 60.04) < 0.1


def test_resolve_destination() -> None:
    codes = {"NLRTM", "SGSIN", "PHMNL"}
    names = {"ROTTERDAM": "NLRTM"}
    assert resolve_destination("NL RTM", codes, names) == ("NLRTM", "unlocode")
    assert resolve_destination("sgsin", codes, names) == ("SGSIN", "unlocode")
    assert resolve_destination("CNSHA>SG-SIN", codes, names) == ("SGSIN", "unlocode")
    assert resolve_destination("Rotterdam", codes, names) == ("NLRTM", "name")
    assert resolve_destination("FOR ORDERS", codes, names) == (None, None)
    assert resolve_destination("XXABC", codes, names) == (None, None)  # not a known port
    assert resolve_destination(None, codes, names) == (None, None)


def test_silence_requires_our_feed_to_have_been_running() -> None:
    from ml.anomaly_detection.runner import feed_continuous

    h = timedelta(hours=1)
    minute = timedelta(minutes=1)
    windows = [(T0 + i * minute, T0 + (i + 1) * minute) for i in range(0, 180)]  # 3 h, no gaps
    assert feed_continuous(windows, T0, T0 + 3 * h)
    gappy = [w for w in windows if not (60 <= (w[0] - T0) / minute < 90)]  # 30 min outage
    assert not feed_continuous(gappy, T0, T0 + 3 * h)
    assert not feed_continuous(windows, T0, T0 + 4 * h)  # feed stopped an hour ago
    assert not feed_continuous([], T0, T0 + h)
