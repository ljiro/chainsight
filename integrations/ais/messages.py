"""Parse and validate AISStream-format messages into internal DTOs.

Message layout follows https://aisstream.io/documentation (verify against current docs):

    {"MessageType": "PositionReport",
     "MetaData": {"MMSI": 259000420, "ShipName": "...", "latitude": .., "longitude": ..,
                  "time_utc": "2022-12-29 18:22:32.318353 +0000 UTC"},
     "Message": {"PositionReport": {"Sog": .., "Cog": .., "TrueHeading": .., ...}}}

AIS "not available" sentinels are mapped to None; impossible values are rejected, never
guessed. Pure functions only: no I/O, no database (see system-design.md §3.1).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

POSITION_TYPES = frozenset(
    {"PositionReport", "StandardClassBPositionReport", "ExtendedClassBPositionReport"}
)
STATIC_TYPES = frozenset({"ShipStaticData"})
SUPPORTED_TYPES = POSITION_TYPES | STATIC_TYPES

# Vessel MMSIs start with a Maritime Identification Digit 2xx-7xx. Other ranges are coast
# stations (00), group calls (0), SAR aircraft (111), AtoN (99), SART/EPIRB (97x), etc.
VESSEL_MMSI_MIN = 200_000_000
VESSEL_MMSI_MAX = 799_999_999

MAX_FUTURE_SKEW = timedelta(minutes=5)

RejectReason = Literal[
    "invalid_payload",
    "invalid_mmsi",
    "non_vessel_mmsi",
    "invalid_position",
    "invalid_timestamp",
    "future_timestamp",
]


class AisRejected(Exception):
    def __init__(self, reason: RejectReason, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class AisPositionReport(_Frozen):
    mmsi: int
    observed_at: datetime
    received_at: datetime
    latitude: float
    longitude: float
    sog_knots: float | None
    cog_deg: float | None
    heading_deg: int | None
    nav_status: int | None
    rate_of_turn: float | None
    position_accuracy_high: bool | None
    message_type: str
    ship_name: str | None

    @property
    def dedup_key(self) -> tuple[int, datetime]:
        return (self.mmsi, self.observed_at)


class AisStaticReport(_Frozen):
    mmsi: int
    observed_at: datetime
    received_at: datetime
    imo: int | None
    name: str | None
    call_sign: str | None
    ship_type: int | None
    length_m: int | None
    beam_m: int | None
    draught_m: float | None
    destination: str | None
    message_type: str


AisReport = AisPositionReport | AisStaticReport

_TIME_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})[ T](?P<time>\d{2}:\d{2}:\d{2})(?:\.(?P<frac>\d{1,9}))?"
    r"\s*(?P<offset>[+-]\d{2}:?\d{2}|Z)?(?:\s+[A-Z]{2,5})?$"
)


def parse_ais_time(value: str) -> datetime:
    """Parse AISStream's Go-style timestamp ('2022-12-29 18:22:32.318353 +0000 UTC').

    Handles nanosecond fractions, which `datetime.strptime` cannot."""
    m = _TIME_RE.match(value.strip())
    if not m:
        raise ValueError(f"unrecognized timestamp: {value!r}")
    frac = (m.group("frac") or "0")[:6].ljust(6, "0")
    base = datetime.fromisoformat(f"{m.group('date')}T{m.group('time')}.{frac}")
    offset = m.group("offset")
    if offset is None or offset == "Z":
        tz: timezone = UTC
    else:
        sign = 1 if offset[0] == "+" else -1
        digits = offset[1:].replace(":", "")
        tz = timezone(sign * timedelta(hours=int(digits[:2]), minutes=int(digits[2:])))
    return base.replace(tzinfo=tz).astimezone(UTC)


def clean_text(value: object, max_len: int) -> str | None:
    """AIS 6-bit text pads with '@'; trim padding/whitespace, upper-case, empty -> None."""
    if not isinstance(value, str):
        return None
    text = value.replace("@", " ").strip().upper()
    text = re.sub(r"\s+", " ", text)
    return text[:max_len] or None


def is_valid_imo(imo: int) -> bool:
    """IMO numbers are 7 digits; the last is a weighted checksum of the first six."""
    if not 1_000_000 <= imo <= 9_999_999:
        return False
    digits = [int(d) for d in str(imo)]
    checksum = sum(d * w for d, w in zip(digits[:6], range(7, 1, -1), strict=True))
    return checksum % 10 == digits[6]


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _sog(value: Any) -> float | None:
    v = _num(value)
    return None if v is None or v < 0 or v >= 102.3 else round(v, 1)  # 102.3 = not available


def _cog(value: Any) -> float | None:
    v = _num(value)
    return None if v is None or v < 0 or v >= 360 else round(v, 1)  # 360 = not available


def _heading(value: Any) -> int | None:
    v = _num(value)
    return None if v is None or v < 0 or v > 359 else int(v)  # 511 = not available


def _rot(value: Any) -> float | None:
    v = _num(value)
    return None if v is None or v <= -128 or v >= 128 else v  # -128 = not available


def _nav_status(value: Any) -> int | None:
    v = _num(value)
    return int(v) if v is not None and 0 <= v <= 15 else None


def _dimension(a: Any, b: Any) -> int | None:
    total = (_num(a) or 0) + (_num(b) or 0)
    return int(total) if total > 0 else None


def parse_message(raw: Mapping[str, Any], *, received_at: datetime) -> AisReport | None:
    """Return a DTO, None for message types we do not ingest, or raise AisRejected."""
    msg_type = raw.get("MessageType")
    if msg_type not in SUPPORTED_TYPES:
        return None
    meta = raw.get("MetaData")
    body = (
        (raw.get("Message") or {}).get(msg_type) if isinstance(raw.get("Message"), dict) else None
    )
    if not isinstance(meta, dict) or not isinstance(body, dict):
        raise AisRejected("invalid_payload", "missing MetaData or Message body")

    mmsi_raw = meta.get("MMSI", body.get("UserID"))
    if (
        isinstance(mmsi_raw, bool)
        or not isinstance(mmsi_raw, int)
        or not 0 < mmsi_raw <= 999_999_999
    ):
        raise AisRejected("invalid_mmsi", repr(mmsi_raw))
    if not VESSEL_MMSI_MIN <= mmsi_raw <= VESSEL_MMSI_MAX:
        raise AisRejected("non_vessel_mmsi", str(mmsi_raw))

    time_raw = meta.get("time_utc")
    if not isinstance(time_raw, str):
        raise AisRejected("invalid_timestamp", "missing time_utc")
    try:
        observed_at = parse_ais_time(time_raw)
    except ValueError as exc:
        raise AisRejected("invalid_timestamp", str(exc)) from exc
    if observed_at > received_at + MAX_FUTURE_SKEW:
        raise AisRejected("future_timestamp", observed_at.isoformat())

    if msg_type in STATIC_TYPES:
        imo_raw = body.get("ImoNumber")
        imo = imo_raw if isinstance(imo_raw, int) and is_valid_imo(imo_raw) else None
        dim_raw = body.get("Dimension")
        dim: dict[str, Any] = dim_raw if isinstance(dim_raw, dict) else {}
        draught = _num(body.get("MaximumStaticDraught"))
        ship_type = _num(body.get("Type"))
        return AisStaticReport(
            mmsi=mmsi_raw,
            observed_at=observed_at,
            received_at=received_at,
            imo=imo,
            name=clean_text(body.get("Name"), 120),
            call_sign=clean_text(body.get("CallSign"), 16),
            ship_type=int(ship_type) if ship_type and 0 < ship_type < 100 else None,
            length_m=_dimension(dim.get("A"), dim.get("B")),
            beam_m=_dimension(dim.get("C"), dim.get("D")),
            draught_m=draught if draught and draught > 0 else None,
            destination=clean_text(body.get("Destination"), 64),
            message_type=msg_type,
        )

    lat = _num(body.get("Latitude", meta.get("latitude")))
    lon = _num(body.get("Longitude", meta.get("longitude")))
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise AisRejected("invalid_position", f"lat={lat} lon={lon}")  # 91/181 = not available
    if lat == 0 and lon == 0:
        raise AisRejected("invalid_position", "null island")

    accuracy = body.get("PositionAccuracy")
    return AisPositionReport(
        mmsi=mmsi_raw,
        observed_at=observed_at,
        received_at=received_at,
        latitude=lat,
        longitude=lon,
        sog_knots=_sog(body.get("Sog")),
        cog_deg=_cog(body.get("Cog")),
        heading_deg=_heading(body.get("TrueHeading")),
        nav_status=_nav_status(body.get("NavigationalStatus")),
        rate_of_turn=_rot(body.get("RateOfTurn")),
        position_accuracy_high=accuracy if isinstance(accuracy, bool) else None,
        message_type=msg_type,
        ship_name=clean_text(meta.get("ShipName"), 120),
    )
