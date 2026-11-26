"""Vessel classification from AIS ship type, and carrier association heuristics."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from database.enums import VesselClass


def vessel_class_from_ship_type(code: int | None) -> VesselClass:
    """Map the AIS ship-type code (ITU-R M.1371) to a coarse class.

    AIS only says "cargo" or "tanker": it cannot tell a container ship from a bulk
    carrier, or an LNG carrier from a crude tanker. Those classes need registry data."""
    if code is None:
        return VesselClass.UNKNOWN
    if code == 30:
        return VesselClass.FISHING
    if code in (31, 32) or 50 <= code <= 59:
        return VesselClass.TUG_SERVICE
    if code in (36, 37):
        return VesselClass.PLEASURE
    if 40 <= code <= 49:
        return VesselClass.HIGH_SPEED
    if 60 <= code <= 69:
        return VesselClass.PASSENGER
    if 70 <= code <= 79:
        return VesselClass.CARGO
    if 80 <= code <= 89:
        return VesselClass.TANKER
    if 20 <= code <= 99:
        return VesselClass.OTHER
    return VesselClass.UNKNOWN


@dataclass(frozen=True)
class CarrierGuess:
    carrier_code: str
    confidence: Decimal
    rule: str


# Branded naming conventions. A name prefix is evidence, not proof: carriers charter
# tonnage and sell slots, so matches are stored with method='name_heuristic' and a
# moderate confidence, and surfaced as "inferred from vessel name" in the UI.
_NAME_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^MAERSK\s"), "maersk"),
    (re.compile(r"^MSC\s"), "msc"),
    (re.compile(r"^CMA\s?CGM\s"), "cma-cgm"),
    (re.compile(r"^COSCO\s"), "cosco"),
    (re.compile(r"^EVER\s"), "evergreen"),
    (re.compile(r"^ONE\s"), "one"),
]
NAME_HEURISTIC_CONFIDENCE = Decimal("0.60")


def guess_carrier_from_name(name: str | None) -> CarrierGuess | None:
    if not name or name.startswith("SYNTHETIC "):
        return None
    for pattern, code in _NAME_RULES:
        if pattern.match(name):
            return CarrierGuess(code, NAME_HEURISTIC_CONFIDENCE, f"name matches {pattern.pattern}")
    return None
