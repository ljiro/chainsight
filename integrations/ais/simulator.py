"""Synthetic AIS feed for development and demos (data mode: synthetic).

Vessels shuttle along hand-drawn sea lanes between real UN/LOCODE port coordinates, dwell
at each end, and report in AISStream message format, so the full ingestion pipeline is
exercised without credentials.

Anti-confusion safeguards:
- MMSIs use MID 200, which is not allocated to any flag state, so they cannot collide
  with real vessels.
- Names start with "SYNTHETIC" and no IMO number is reported.
- Positions are a deterministic function of wall-clock time: restarts continue smoothly.

Lane waypoints are approximate and only meant to keep tracks at sea. They are not
navigational routes.
"""

from __future__ import annotations

import asyncio
import bisect
import itertools
import random
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from pyproj import Geod

from core.timeutils import utcnow
from integrations.ais.providers import AisProvider, Clock, RawAisMessage, Sleep

GEOD = Geod(ellps="WGS84")
EPOCH = datetime(2026, 1, 1, tzinfo=UTC)
SYNTHETIC_MID = 200
NM = 1852.0

LatLon = tuple[float, float]

# Endpoints are exact UN/LOCODE coordinates so arrivals fall inside the port geofences.
NLRTM, DEHAM, USNYC = (51.9167, 4.5), (53.5167, 9.9333), (40.7, -74.0)
SGSIN, HKHKG, CNSHG = (1.2833, 103.85), (22.3167, 114.1667), (30.6333, 122.0667)
KRPUS, JPYOK, USLGB = (35.1333, 129.05), (35.45, 139.65), (33.7667, -118.1833)
SARTA, QARLF = (26.6333, 50.15), (25.9333, 51.5333)

_MALACCA_W_TO_E: list[LatLon] = [
    (6.1, 94.8),
    (5.8, 97.5),
    (3.3, 100.4),
    (2.1, 102.0),
    (1.25, 103.55),
]
_GULF_TO_SRI_LANKA: list[LatLon] = [
    (26.5, 53.0), (26.2, 55.0), (26.45, 56.5), (25.5, 57.3), (22.5, 60.5),
    (12.0, 70.0), (7.0, 77.0), (5.6, 80.5),
]  # fmt: skip
_CHANNEL_FROM_RTM: list[LatLon] = [
    (51.98, 4.05), (51.9, 3.5), (51.05, 1.6), (50.5, 0.0), (49.7, -3.0),
]  # fmt: skip

LANES: dict[str, tuple[str, list[LatLon]]] = {
    "AEX": ("Asia-Europe via Suez", [
        SGSIN, (1.25, 103.55), (2.1, 102.0), (3.3, 100.4), (5.8, 97.5), (6.1, 94.8), (5.6, 80.5),
        (13.0, 51.0), (12.4, 44.0), (12.6, 43.35), (15.0, 42.0), (20.0, 38.7), (27.0, 34.3),
        (29.6, 32.6), (30.6, 32.33), (31.35, 32.35), (32.5, 29.0), (33.8, 22.0), (37.4, 11.4),
        (37.8, 5.0), (36.0, -2.0), (35.95, -5.6), (36.6, -9.5), (43.3, -10.0), (48.2, -6.0),
        *reversed(_CHANNEL_FROM_RTM), NLRTM,
    ]),
    "TPX": ("Transpacific", [
        CNSHG, (30.4, 123.0), (29.8, 129.0), (30.2, 132.0), (32.5, 141.5), (40.0, 165.0),
        (41.0, -170.0), (38.0, -140.0), (33.55, -118.3), USLGB,
    ]),
    "IAX": ("Intra-Asia", [
        SGSIN, (1.4, 104.4), (5.0, 106.0), (11.0, 110.5), (18.0, 113.0), (22.15, 114.25),
        HKHKG, (22.5, 116.0), (24.3, 119.0), (27.5, 121.8), (30.5, 122.5), CNSHG,
    ]),
    "KSX": ("Shanghai-Busan", [CNSHG, (31.5, 124.5), (33.0, 127.6), (34.3, 128.3), KRPUS]),
    "TAX": ("Transatlantic", [
        NLRTM, *_CHANNEL_FROM_RTM, (49.3, -6.5), (47.0, -20.0), (42.0, -50.0), (40.3, -70.0),
        (40.45, -73.8), USNYC,
    ]),
    "NSF": ("North Sea feeder", [
        NLRTM, (51.98, 4.05), (52.5, 4.2), (53.8, 6.0), (54.0, 7.8), (53.9, 8.6), (53.8, 9.2),
        (53.6, 9.55), DEHAM,
    ]),
    "MEG": ("Arabian Gulf-Singapore (tanker)", [
        SARTA, (27.0, 51.0), *_GULF_TO_SRI_LANKA, *_MALACCA_W_TO_E, SGSIN,
    ]),
    "LNG": ("Ras Laffan-Yokohama (gas carrier)", [
        QARLF, (26.3, 51.8), *_GULF_TO_SRI_LANKA, *_MALACCA_W_TO_E, SGSIN, (1.4, 104.4),
        (5.0, 106.0), (11.0, 110.5), (18.0, 113.0), (20.8, 120.8), (25.0, 126.0),
        (30.0, 133.0), (34.6, 139.9), JPYOK,
    ]),
}  # fmt: skip

# lane, vessel count, AIS ship type (70 = cargo, 80 = tanker), speed range (kn), dwell (h)
FLEET_PLAN: list[tuple[str, int, int, tuple[float, float], float]] = [
    ("AEX", 6, 70, (16.0, 20.0), 8.0),
    ("TPX", 4, 70, (16.0, 19.0), 8.0),
    ("IAX", 4, 70, (14.0, 17.0), 6.0),
    ("KSX", 2, 70, (12.0, 14.0), 4.0),
    ("TAX", 3, 70, (15.0, 18.0), 8.0),
    ("NSF", 2, 70, (11.0, 13.0), 4.0),
    ("MEG", 2, 80, (12.5, 14.5), 12.0),
    ("LNG", 1, 80, (17.0, 19.0), 12.0),
]


class Route:
    """Polyline over the ellipsoid with distance-based interpolation."""

    def __init__(self, waypoints: list[LatLon]) -> None:
        if len(waypoints) < 2:
            raise ValueError("a route needs at least two waypoints")
        self.waypoints = waypoints
        self.cumulative = [0.0]
        self.azimuths: list[float] = []
        for (lat1, lon1), (lat2, lon2) in itertools.pairwise(waypoints):
            az, _, dist = GEOD.inv(lon1, lat1, lon2, lat2)
            self.azimuths.append(az % 360)
            self.cumulative.append(self.cumulative[-1] + dist)

    @property
    def length_m(self) -> float:
        return self.cumulative[-1]

    def at(self, distance_m: float) -> tuple[float, float, float]:
        """(lat, lon, course_deg) at `distance_m` from the start."""
        d = min(max(distance_m, 0.0), self.length_m)
        i = max(0, min(bisect.bisect_right(self.cumulative, d) - 1, len(self.azimuths) - 1))
        lat1, lon1 = self.waypoints[i]
        lon, lat, back_azimuth = GEOD.fwd(lon1, lat1, self.azimuths[i], d - self.cumulative[i])
        # Course changes along a great circle; the local course is the reversed back azimuth.
        course = (back_azimuth + 180) % 360
        return lat, (lon + 180) % 360 - 180, course


@dataclass(frozen=True)
class SimVessel:
    mmsi: int
    name: str
    call_sign: str
    ship_type: int
    lane: str
    route: Route
    speed_kn: float
    dwell_s: float
    offset_s: float
    length_m: int
    beam_m: int
    draught_m: float

    @property
    def sail_s(self) -> float:
        return self.route.length_m / (self.speed_kn * NM / 3600)

    @property
    def period_s(self) -> float:
        return 2 * (self.sail_s + self.dwell_s)

    def state_at(self, t: datetime) -> dict[str, Any]:
        """Shuttle cycle: sail out, dwell at far end, sail back, dwell at origin."""
        phase = ((t - EPOCH).total_seconds() + self.offset_s) % self.period_s
        sail, dwell, length = self.sail_s, self.dwell_s, self.route.length_m
        if phase < sail:
            lat, lon, course = self.route.at(length * phase / sail)
            return _underway(lat, lon, course, self.speed_kn, phase)
        if phase < sail + dwell:
            lat, lon, _ = self.route.at(length)
            return _moored(lat, lon)
        if phase < 2 * sail + dwell:
            lat, lon, course = self.route.at(length * (1 - (phase - sail - dwell) / sail))
            return _underway(lat, lon, (course + 180) % 360, self.speed_kn, phase)
        lat, lon, _ = self.route.at(0.0)
        return _moored(lat, lon)

    def destination_at(self, t: datetime) -> str:
        phase = ((t - EPOCH).total_seconds() + self.offset_s) % self.period_s
        heading_out = phase < self.sail_s + self.dwell_s
        end = self.route.waypoints[-1] if heading_out else self.route.waypoints[0]
        return _ENDPOINT_NAMES.get(end, "UNKNOWN")


_ENDPOINT_NAMES = {
    NLRTM: "NL RTM", DEHAM: "DE HAM", USNYC: "US NYC", SGSIN: "SG SIN", HKHKG: "HK HKG",
    CNSHG: "CN SHG", KRPUS: "KR PUS", JPYOK: "JP YOK", USLGB: "US LGB", SARTA: "SA RTA",
    QARLF: "QA RLF",
}  # fmt: skip


def _underway(lat: float, lon: float, course: float, speed: float, phase: float) -> dict[str, Any]:
    wobble = ((phase // 600) % 7 - 3) * 0.1  # small deterministic speed variation
    return {
        "lat": lat,
        "lon": lon,
        "cog": round(course, 1),
        "sog": round(speed + wobble, 1),
        "nav": 0,
    }


def _moored(lat: float, lon: float) -> dict[str, Any]:
    return {"lat": lat, "lon": lon, "cog": None, "sog": 0.0, "nav": 5}


def build_fleet(vessel_count: int | None = None, seed: int = 7) -> list[SimVessel]:
    rng = random.Random(seed)  # noqa: S311 - simulation, not security
    routes = {code: Route(points) for code, (_, points) in LANES.items()}
    fleet: list[SimVessel] = []
    for lane, count, ship_type, (lo, hi), dwell_h in FLEET_PLAN:
        for _ in range(count):
            n = len(fleet) + 1
            tanker = ship_type >= 80
            vessel = SimVessel(
                mmsi=SYNTHETIC_MID * 1_000_000 + n,
                name=f"SYNTHETIC {lane} {n:02d}",
                call_sign=f"SYN{n:03d}",
                ship_type=ship_type,
                lane=lane,
                route=routes[lane],
                speed_kn=round(rng.uniform(lo, hi), 1),
                dwell_s=dwell_h * 3600,
                offset_s=0.0,
                length_m=rng.choice([250, 280, 300])
                if tanker
                else rng.choice([200, 300, 366, 400]),
                beam_m=rng.choice([44, 48]) if tanker else rng.choice([32, 48, 51, 59]),
                draught_m=round(rng.uniform(11.0, 15.5), 1),
            )
            # Spread vessels across the cycle so the map is populated immediately.
            fleet.append(replace(vessel, offset_s=rng.uniform(0, vessel.period_s)))
    return fleet[:vessel_count] if vessel_count else fleet


def _go_time(t: datetime) -> str:
    return t.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f +0000 UTC")


def position_message(v: SimVessel, t: datetime) -> dict[str, Any]:
    s = v.state_at(t)
    return {
        "MessageType": "PositionReport",
        "MetaData": {
            "MMSI": v.mmsi,
            "ShipName": v.name,
            "latitude": s["lat"],
            "longitude": s["lon"],
            "time_utc": _go_time(t),
        },
        "Message": {
            "PositionReport": {
                "UserID": v.mmsi,
                "Latitude": s["lat"],
                "Longitude": s["lon"],
                "Sog": s["sog"],
                "Cog": s["cog"] if s["cog"] is not None else 360,
                "TrueHeading": round(s["cog"]) % 360 if s["cog"] is not None else 511,
                "NavigationalStatus": s["nav"],
                "RateOfTurn": 0,
                "PositionAccuracy": True,
                "Valid": True,
            }
        },
    }


def static_message(v: SimVessel, t: datetime) -> dict[str, Any]:
    half_len, half_beam = v.length_m // 2, v.beam_m // 2
    return {
        "MessageType": "ShipStaticData",
        "MetaData": {"MMSI": v.mmsi, "ShipName": v.name, "time_utc": _go_time(t)},
        "Message": {
            "ShipStaticData": {
                "UserID": v.mmsi,
                "Name": v.name,
                "CallSign": v.call_sign,
                "ImoNumber": 0,
                "Type": v.ship_type,
                "Dimension": {"A": half_len, "B": v.length_m - half_len, "C": half_beam,
                              "D": v.beam_m - half_beam},
                "MaximumStaticDraught": v.draught_m,
                "Destination": v.destination_at(t),
                "Valid": True,
            }
        },
    }  # fmt: skip


class SimulatorProvider(AisProvider):
    source_key = "ais-simulator"
    receiver_type = "synthetic"

    def __init__(
        self,
        vessel_count: int | None = None,
        interval_s: float = 30.0,
        static_every: int = 12,
        *,
        seed: int = 7,
        sleep: Sleep = asyncio.sleep,
        clock: Clock = utcnow,
    ) -> None:
        self.fleet = build_fleet(vessel_count, seed)
        self.interval_s = interval_s
        self.static_every = static_every
        self._sleep = sleep
        self._clock = clock

    async def stream(self) -> AsyncIterator[RawAisMessage]:
        tick = 0
        step = self.interval_s / len(self.fleet)  # stagger vessels across the interval
        while True:
            vessel = self.fleet[tick % len(self.fleet)]
            round_no = tick // len(self.fleet)
            now = self._clock()
            if round_no % self.static_every == 0:
                yield RawAisMessage(static_message(vessel, now), now)
            yield RawAisMessage(position_message(vessel, now), now)
            tick += 1
            await self._sleep(step)
