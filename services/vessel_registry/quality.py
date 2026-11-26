"""Position data-quality flags (bitmask stored in vessel_positions.quality_flags)."""

from __future__ import annotations

import math
from datetime import datetime
from enum import IntFlag

MAX_PLAUSIBLE_SPEED_KN = 60.0
EARTH_RADIUS_M = 6_371_008.8


class QualityFlag(IntFlag):
    NONE = 0
    LOW_ACCURACY = 1  # position accuracy bit not set (> 10 m)
    MISSING_SOG = 2
    MISSING_COG = 4
    IMPLIED_SPEED_JUMP = 8  # distance from previous fix implies an impossible speed
    OUT_OF_ORDER = 16  # older than the latest known fix for this vessel


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def implied_speed_kn(
    prev: tuple[datetime, float, float], cur: tuple[datetime, float, float]
) -> float | None:
    dt = (cur[0] - prev[0]).total_seconds()
    if dt <= 0:
        return None
    return haversine_m(prev[1], prev[2], cur[1], cur[2]) / dt * 3600 / 1852


def quality_flags(
    *,
    observed_at: datetime,
    lat: float,
    lon: float,
    sog: float | None,
    cog: float | None,
    accuracy_high: bool | None,
    previous: tuple[datetime, float, float] | None,
) -> QualityFlag:
    flags = QualityFlag.NONE
    if accuracy_high is False:
        flags |= QualityFlag.LOW_ACCURACY
    if sog is None:
        flags |= QualityFlag.MISSING_SOG
    if cog is None:
        flags |= QualityFlag.MISSING_COG
    if previous is not None:
        if observed_at < previous[0]:
            flags |= QualityFlag.OUT_OF_ORDER
        else:
            speed = implied_speed_kn(previous, (observed_at, lat, lon))
            if speed is not None and speed > MAX_PLAUSIBLE_SPEED_KN:
                flags |= QualityFlag.IMPLIED_SPEED_JUMP
    return flags
