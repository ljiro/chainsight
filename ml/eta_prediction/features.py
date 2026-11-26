"""ETA features (pure). Everything is computed from data available at the snapshot time."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import datetime

from ml.anomaly_detection.rules import haversine_nm

FEATURES = [
    "remaining_distance_nm",
    "sog_knots",
    "recent_mean_sog_knots",
    "bearing_offset_deg",
    "baseline_hours",
    "length_m",
    "is_tanker",
    "hour_utc",
    "day_of_week",
]
MIN_SPEED_KN = 1.0


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


@dataclass(frozen=True)
class EtaFeatures:
    remaining_distance_nm: float
    sog_knots: float
    recent_mean_sog_knots: float
    bearing_offset_deg: float
    baseline_hours: float
    length_m: float
    is_tanker: int
    hour_utc: int
    day_of_week: int

    def as_dict(self) -> dict[str, float | int]:
        return asdict(self)


def baseline_hours(distance_nm: float, recent_mean_sog: float | None, sog: float | None) -> float:
    """Physics baseline: remaining great-circle distance / recent average speed."""
    speed = recent_mean_sog if recent_mean_sog and recent_mean_sog >= MIN_SPEED_KN else sog
    return distance_nm / max(speed or MIN_SPEED_KN, MIN_SPEED_KN)


def build_features(
    *,
    at: datetime,
    lat: float,
    lon: float,
    sog: float | None,
    cog: float | None,
    recent_mean_sog: float | None,
    target_lat: float,
    target_lon: float,
    length_m: float | None,
    vessel_class: str | None,
) -> EtaFeatures:
    distance = haversine_nm(lat, lon, target_lat, target_lon)
    bearing = bearing_deg(lat, lon, target_lat, target_lon)
    offset = abs((cog - bearing + 180) % 360 - 180) if cog is not None else 90.0
    return EtaFeatures(
        remaining_distance_nm=round(distance, 2),
        sog_knots=float(sog or 0.0),
        recent_mean_sog_knots=float(recent_mean_sog or sog or 0.0),
        bearing_offset_deg=round(offset, 1),
        baseline_hours=round(baseline_hours(distance, recent_mean_sog, sog), 3),
        length_m=float(length_m or 0.0),
        is_tanker=int(vessel_class in ("tanker", "lng_carrier", "lpg_carrier")),
        hour_utc=at.hour,
        day_of_week=at.weekday(),
    )
