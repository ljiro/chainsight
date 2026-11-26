"""UN/LOCODE code-list parser (seaports only).

Input is the CSV published by the datasets/un-locode project (a mirror of UNECE's
UN/LOCODE releases) with the columns:
    Change, Country, Location, Name, NameWoDiacritics, Subdivision, Status, Function,
    Date, IATA, Coordinates, Remarks

- Function position 1 == "1" marks a port.
- Coordinates are degrees+minutes, e.g. "5155N 00430E" (about 1.85 km precision). They
  locate the place, which is sometimes the city rather than the terminal.
- Change == "X" marks entries scheduled for removal; those are skipped.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

_COORD_RE = re.compile(r"^(\d{2})(\d{2})([NS])\s+(\d{3})(\d{2})([EW])$")


@dataclass(frozen=True, slots=True)
class PortRecord:
    unlocode: str
    name: str
    country_code: str
    subdivision: str | None
    latitude: float
    longitude: float
    functions: str


def parse_coordinates(value: str) -> tuple[float, float] | None:
    m = _COORD_RE.match(value.strip())
    if not m:
        return None
    lat_d, lat_m, ns, lon_d, lon_m, ew = m.groups()
    if int(lat_m) >= 60 or int(lon_m) >= 60:
        return None
    lat = int(lat_d) + int(lat_m) / 60
    lon = int(lon_d) + int(lon_m) / 60
    if lat > 90 or lon > 180:
        return None
    return (-lat if ns == "S" else lat, -lon if ew == "W" else lon)


def iter_seaports(lines: Iterable[str]) -> Iterator[PortRecord]:
    """Yield valid seaports; silently skips rows that are not ports or lack coordinates."""
    for row in csv.DictReader(lines):
        function = (row.get("Function") or "").strip()
        if not function.startswith("1") or (row.get("Change") or "").strip() == "X":
            continue
        country = (row.get("Country") or "").strip().upper()
        location = (row.get("Location") or "").strip().upper()
        if not (len(country) == 2 and len(location) == 3):
            continue
        coords = parse_coordinates(row.get("Coordinates") or "")
        if coords is None:
            continue
        name = (row.get("NameWoDiacritics") or row.get("Name") or "").strip()
        if not name:
            continue
        yield PortRecord(
            unlocode=f"{country}{location}",
            name=name[:200],
            country_code=country,
            subdivision=(row.get("Subdivision") or "").strip() or None,
            latitude=round(coords[0], 5),
            longitude=round(coords[1], 5),
            functions=function,
        )
