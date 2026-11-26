"""FRED (Federal Reserve Bank of St. Louis) daily series.

With `FRED_API_KEY`, the official API (api.stlouisfed.org) is used. Without one, the public
CSV download that FRED offers on every series page (fredgraph.csv) is used. That is a
documented public download, not a workaround for authentication. Some series are third-party
data (EIA, ICE, ...) with their own terms, noted per series.
"""

from __future__ import annotations

import csv
import io
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

import httpx

from integrations.research.availability import NextDay
from integrations.research.http import ResearchSourceError, get_with_retries

API_URL = "https://api.stlouisfed.org/fred/series/observations"
CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"


@dataclass(frozen=True)
class FredSeries:
    series_id: str
    name: str
    unit: str
    original_source: str


# Daily energy prices: the most direct market counterpart of tanker and chokepoint traffic.
DEFAULT_SERIES = (
    FredSeries("DCOILBRENTEU", "Brent crude oil spot price (Europe)", "USD/bbl", "U.S. EIA"),
    FredSeries("DCOILWTICO", "WTI crude oil spot price (Cushing, OK)", "USD/bbl", "U.S. EIA"),
    FredSeries("DHHNGSP", "Henry Hub natural gas spot price", "USD/MMBtu", "U.S. EIA"),
)
# A daily closing price is known by the end of its trading day.
AVAILABILITY = NextDay()


class FredClient:
    def __init__(
        self,
        api_key: str | None = None,
        *,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._key = api_key
        self._client = client or httpx.Client(timeout=60.0)
        self._sleep = sleep

    @property
    def method(self) -> str:
        return "api" if self._key else "public_csv"

    def observations(self, series_id: str, start: date | None = None) -> list[tuple[date, float]]:
        """(day, value) pairs, oldest first; missing values ('.' or blank) are skipped."""
        if self._key:
            params = {"series_id": series_id, "api_key": self._key, "file_type": "json"}
            if start:
                params["observation_start"] = start.isoformat()
            resp = get_with_retries(
                self._client, API_URL, params, sleep=self._sleep, redact=("api_key",)
            )
            rows = resp.json().get("observations", [])
            return [
                (date.fromisoformat(r["date"]), float(r["value"]))
                for r in rows
                if _is_number(r.get("value"))
            ]
        params = {"id": series_id}
        if start:
            params["cosd"] = start.isoformat()
        resp = get_with_retries(self._client, CSV_URL, params, sleep=self._sleep)
        return parse_csv(resp.text, series_id)


def _is_number(v: object) -> bool:
    try:
        float(str(v))
    except ValueError:
        return False
    return True


def parse_csv(text: str, series_id: str) -> list[tuple[date, float]]:
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if not header or len(header) < 2 or header[1].strip() != series_id:
        raise ResearchSourceError(f"unexpected FRED CSV header for {series_id}: {header}")
    out = []
    for row in reader:
        if len(row) >= 2 and _is_number(row[1]):
            out.append((date.fromisoformat(row[0]), float(row[1])))
    return out
