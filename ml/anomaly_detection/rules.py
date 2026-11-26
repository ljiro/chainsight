"""Interpretable, rule-based vessel anomaly detectors.

Each detector looks at one vessel's recent, time-ordered fixes and returns an Anomaly or
None. Rules are deliberately simple and explainable (every alert carries its evidence),
forming the baseline any learned detector must beat. Known limitation: great-circle
distances ignore coastlines, so the route-deviation rule only runs far from the destination.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal

AnomalyType = Literal[
    "unexpected_stop", "speed_drop", "ais_silence", "long_port_wait", "route_deviation"
]
STATIONARY_NAV = {1, 5}  # at anchor, moored


@dataclass(frozen=True)
class Fix:
    t: datetime
    lat: float
    lon: float
    sog: float | None
    nav_status: int | None
    in_port: bool


@dataclass(frozen=True)
class Anomaly:
    type: AnomalyType
    severity: Literal["info", "warning", "critical"]
    title: str
    message: str
    started_at: datetime
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RuleConfig:
    stop_sog_kn: float = 0.5
    stop_min_duration: timedelta = timedelta(minutes=60)
    speed_drop_ratio: float = 0.4
    speed_drop_min_before_kn: float = 10.0
    silence_after: timedelta = timedelta(hours=2)
    silence_max: timedelta = timedelta(hours=48)
    port_wait_hours: float = 72.0
    port_wait_median_factor: float = 3.0
    deviation_min_distance_nm: float = 100.0
    deviation_window: timedelta = timedelta(hours=1)
    deviation_min_increase_nm: float = 20.0


def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6_371_008.8 * math.asin(math.sqrt(a)) / 1852


def unexpected_stop(fixes: list[Fix], cfg: RuleConfig) -> Anomaly | None:
    """Stopped at sea, outside any port geofence, while still reporting 'under way'."""
    run: list[Fix] = []
    for f in fixes:
        stopped = f.sog is not None and f.sog <= cfg.stop_sog_kn
        if stopped and not f.in_port and f.nav_status not in STATIONARY_NAV:
            run.append(f)
        else:
            run = []
    if len(run) >= 2 and run[-1].t - run[0].t >= cfg.stop_min_duration and run[-1] is fixes[-1]:
        minutes = int((run[-1].t - run[0].t).total_seconds() // 60)
        return Anomaly(
            "unexpected_stop",
            "warning",
            "Unexpected stop at sea",
            f"Stationary for {minutes} min outside any port area while reporting under way.",
            run[0].t,
            {
                "minutes": minutes,
                "lat": run[-1].lat,
                "lon": run[-1].lon,
                "nav_status": run[-1].nav_status,
            },
        )
    return None


def speed_drop(fixes: list[Fix], cfg: RuleConfig) -> Anomaly | None:
    """Median speed over the last 15 min fell below `ratio` x the median of the 30 min before."""
    if not fixes:
        return None
    now = fixes[-1].t
    recent = [f.sog for f in fixes if f.sog is not None and now - f.t <= timedelta(minutes=15)]
    before = [
        f.sog
        for f in fixes
        if f.sog is not None and timedelta(minutes=15) < now - f.t <= timedelta(minutes=45)
    ]
    if len(recent) < 2 or len(before) < 2 or fixes[-1].in_port:
        return None
    m_recent, m_before = statistics.median(recent), statistics.median(before)
    if m_before >= cfg.speed_drop_min_before_kn and m_recent <= cfg.speed_drop_ratio * m_before:
        return Anomaly(
            "speed_drop",
            "info",
            "Sudden speed drop",
            f"Speed fell from {m_before:.1f} kn to {m_recent:.1f} kn within 45 min, outside port.",
            now - timedelta(minutes=15),
            {"before_kn": m_before, "after_kn": m_recent},
        )
    return None


def ais_silence(last: Fix | None, now: datetime, cfg: RuleConfig) -> Anomaly | None:
    """No reports for a while from a vessel that was moving. Often a reception gap, so info."""
    if last is None or last.sog is None or last.sog < 3 or last.in_port:
        return None
    silent = now - last.t
    if cfg.silence_after <= silent <= cfg.silence_max:
        hours = round(silent.total_seconds() / 3600, 1)
        return Anomaly(
            "ais_silence",
            "info",
            "AIS reports missing",
            f"No position for {hours} h after reporting {last.sog:.1f} kn underway. "
            "May be a receiver coverage gap rather than the vessel.",
            last.t,
            {"silent_hours": hours, "last_sog_kn": last.sog},
        )
    return None


def long_port_wait(
    arrived_at: datetime | None, now: datetime, port_median_hours: float | None, cfg: RuleConfig
) -> Anomaly | None:
    if arrived_at is None:  # arrival not observed: duration unknown, so no claim is made
        return None
    hours = (now - arrived_at).total_seconds() / 3600
    limit = cfg.port_wait_hours
    basis = f"{cfg.port_wait_hours:.0f} h default"
    if port_median_hours:
        limit = max(24.0, cfg.port_wait_median_factor * port_median_hours)
        basis = (
            f"{cfg.port_wait_median_factor:g}x this port's median call ({port_median_hours:.0f} h)"
        )
    if hours > limit:
        return Anomaly(
            "long_port_wait",
            "info",
            "Unusually long port stay",
            f"In port for {hours:.0f} h (threshold: {basis}).",
            arrived_at,
            {"hours_in_port": round(hours, 1), "threshold_hours": round(limit, 1)},
        )
    return None


def route_deviation(
    fixes: list[Fix], destination: tuple[float, float] | None, cfg: RuleConfig
) -> Anomaly | None:
    """Moving away from the reported destination while underway and far from it."""
    if destination is None or len(fixes) < 3:
        return None
    now = fixes[-1].t
    window = [f for f in fixes if now - f.t <= cfg.deviation_window]
    moving = [f for f in window if f.sog is not None and f.sog > 5]
    if len(moving) < 3 or window[-1].t - window[0].t < cfg.deviation_window / 2:
        return None
    d_start = haversine_nm(window[0].lat, window[0].lon, *destination)
    d_end = haversine_nm(window[-1].lat, window[-1].lon, *destination)
    if d_end > cfg.deviation_min_distance_nm and d_end - d_start >= cfg.deviation_min_increase_nm:
        return Anomaly(
            "route_deviation",
            "warning",
            "Moving away from reported destination",
            f"Distance to the reported destination grew from {d_start:.0f} nm to {d_end:.0f} nm "
            "in the last hour while underway.",
            window[0].t,
            {"distance_start_nm": round(d_start, 1), "distance_end_nm": round(d_end, 1)},
        )
    return None
