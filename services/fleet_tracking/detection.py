"""Pure detection logic: AIS geofence port calls and vessels approaching ports."""

from __future__ import annotations

import math
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

STOPPED_SOG_KN = 0.5  # at or below: considered stopped
DEPARTED_SOG_KN = 1.0  # outside the area AND faster than this: departed
STATIONARY_NAV_STATUS = frozenset({1, 5})  # at anchor, moored


@dataclass(frozen=True, slots=True)
class GeofenceObservation:
    observed_at: datetime
    port_id: uuid.UUID | None  # nearest port whose geofence contains the fix
    sog_knots: float | None
    nav_status: int | None


@dataclass(frozen=True, slots=True)
class PortEvent:
    kind: Literal["arrival", "departure"]
    port_id: uuid.UUID
    at: datetime


def _is_stopped(o: GeofenceObservation) -> bool:
    return (o.sog_knots is not None and o.sog_knots <= STOPPED_SOG_KN) or (
        o.nav_status in STATIONARY_NAV_STATUS
    )


def detect_port_events(
    open_port_id: uuid.UUID | None, observations: Iterable[GeofenceObservation]
) -> tuple[list[PortEvent], uuid.UUID | None]:
    """Run the arrival/departure state machine over time-ordered fixes for one vessel.

    - Arrival: inside a geofence and stopped (slow, anchored, or moored). Passing through
      a port area at speed is a transit, not a call.
    - Departure: outside the open call's geofence and moving. A stationary fix that
      jitters just outside the edge does not end the call.

    Returns the events and the port of the call still open afterwards."""
    events: list[PortEvent] = []
    current = open_port_id
    for o in sorted(observations, key=lambda x: x.observed_at):
        if current is not None and o.port_id != current:
            moving = o.sog_knots is None or o.sog_knots > DEPARTED_SOG_KN
            if moving or o.port_id is not None:
                events.append(PortEvent("departure", current, o.observed_at))
                current = None
        if current is None and o.port_id is not None and _is_stopped(o):
            events.append(PortEvent("arrival", o.port_id, o.observed_at))
            current = o.port_id
    return events, current


def initial_bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def angle_diff_deg(a: float, b: float) -> float:
    return abs((a - b + 180) % 360 - 180)


def is_approaching(
    *,
    vessel_lat: float,
    vessel_lon: float,
    cog_deg: float | None,
    sog_knots: float | None,
    port_lat: float,
    port_lon: float,
    min_sog_kn: float = 3.0,
    max_course_offset_deg: float = 30.0,
) -> bool:
    """Underway and pointed at the port (course within +/- `max_course_offset_deg`)."""
    if cog_deg is None or sog_knots is None or sog_knots < min_sog_kn:
        return False
    bearing = initial_bearing_deg(vessel_lat, vessel_lon, port_lat, port_lon)
    return angle_diff_deg(cog_deg, bearing) <= max_course_offset_deg
