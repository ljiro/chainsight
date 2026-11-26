"""IMF PortWatch daily port and chokepoint activity (ArcGIS Feature Services, open data).

Portal: https://portwatch.imf.org. Port calls and trade volumes are IMF *estimates* derived
from satellite AIS, not customs statistics. Data is published weekly (observed: Tuesdays,
ports through the previous Friday, chokepoints through the previous Sunday) and recent
values can be revised.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import date

import httpx

from integrations.research.availability import WeeklyRelease
from integrations.research.http import ResearchSourceError, get_with_retries

BASE_URL = "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services"
LAYERS = {"port": "Daily_Ports_Data", "chokepoint": "Daily_Chokepoints_Data"}
PAGE_SIZE = 1000  # the services' maxRecordCount

# metric -> (unit, description). Tonnage figures are IMF model estimates.
PORT_METRICS: dict[str, tuple[str, str]] = {
    "portcalls": ("calls", "Port calls, all vessel types"),
    "portcalls_tanker": ("calls", "Tanker port calls"),
    "portcalls_container": ("calls", "Container ship port calls"),
    "portcalls_dry_bulk": ("calls", "Dry bulk port calls"),
    "import": ("tonnes", "Estimated import volume"),
    "export": ("tonnes", "Estimated export volume"),
    "import_tanker": ("tonnes", "Estimated tanker import volume"),
    "export_tanker": ("tonnes", "Estimated tanker export volume"),
    "import_container": ("tonnes", "Estimated container import volume"),
    "export_container": ("tonnes", "Estimated container export volume"),
}
CHOKEPOINT_METRICS: dict[str, tuple[str, str]] = {
    "n_total": ("transits", "Transits, all vessel types"),
    "n_tanker": ("transits", "Tanker transits"),
    "n_container": ("transits", "Container ship transits"),
    "n_dry_bulk": ("transits", "Dry bulk transits"),
    "capacity": ("dwt", "Transit capacity (deadweight tonnes)"),
    "capacity_tanker": ("dwt", "Tanker transit capacity (deadweight tonnes)"),
}
METRICS = {"port": PORT_METRICS, "chokepoint": CHOKEPOINT_METRICS}

AVAILABILITY = {
    "port": WeeklyRelease(weekday=1, hour=12, min_lag_days=4),
    "chokepoint": WeeklyRelease(weekday=1, hour=12, min_lag_days=2),
}

# A priori port universe: large, long-established hubs chosen by name before looking at any
# results (to avoid selecting ports for how well they "work"). Override in settings.
DEFAULT_PORTS: dict[str, str] = {
    "port1114": "Rotterdam",
    "port1201": "Singapore",
    "port1188": "Shanghai",
    "port57": "Antwerp",
    "port664": "Los Angeles-Long Beach",
    "port481": "Houston (US-TX)",
    "port1091": "Ras Tanura",
    "port362": "Fujairah",
}  # ids verified against the PortWatch_ports_database layer


@dataclass(frozen=True)
class DailyRecord:
    kind: str  # port | chokepoint
    entity_id: str
    entity_name: str
    country_iso3: str | None
    day: date
    metrics: dict[str, float]


class PortWatchClient:
    def __init__(
        self,
        base_url: str = BASE_URL,
        *,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        page_pause_s: float = 0.25,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=60.0)
        self._sleep = sleep
        self._pause = page_pause_s

    def _query(self, kind: str, params: dict[str, str]) -> dict[str, object]:
        url = f"{self.base_url}/{LAYERS[kind]}/FeatureServer/0/query"
        resp = get_with_retries(self._client, url, params | {"f": "json"}, sleep=self._sleep)
        payload = resp.json()
        if not isinstance(payload, dict) or "error" in payload:
            err = payload.get("error") if isinstance(payload, dict) else None
            raise ResearchSourceError(f"PortWatch query failed: {err}")
        return payload

    def entities(self, kind: str) -> dict[str, str]:
        """entity id -> name, for every entity in the layer."""
        payload = self._query(
            kind,
            {"where": "1=1", "outFields": "portid,portname", "returnDistinctValues": "true",
             "returnGeometry": "false"},
        )  # fmt: skip
        feats = payload.get("features") or []
        assert isinstance(feats, list)
        return {f["attributes"]["portid"]: f["attributes"]["portname"] for f in feats}

    def daily(
        self, kind: str, entity_ids: Sequence[str], start: date | None = None
    ) -> Iterator[DailyRecord]:
        """Daily records for the entities, oldest first, paging through the service."""
        if not entity_ids:
            return
        if any("'" in e for e in entity_ids):
            raise ValueError("invalid entity id")
        metrics = METRICS[kind]
        where = "portid IN (" + ",".join(f"'{e}'" for e in entity_ids) + ")"
        if start is not None:
            where += f" AND date >= DATE '{start.isoformat()}'"
        fields = ["date", "portid", "portname", *metrics] + (["ISO3"] if kind == "port" else [])
        offset = 0
        while True:
            payload = self._query(
                kind,
                {"where": where, "outFields": ",".join(fields), "orderByFields": "date ASC,portid ASC",
                 "resultOffset": str(offset), "resultRecordCount": str(PAGE_SIZE), "returnGeometry": "false"},
            )  # fmt: skip
            feats = payload.get("features") or []
            assert isinstance(feats, list)
            for f in feats:
                yield parse_record(kind, f["attributes"])
            if not payload.get("exceededTransferLimit") or not feats:
                return
            offset += len(feats)
            self._sleep(self._pause)


def parse_record(kind: str, a: dict[str, object]) -> DailyRecord:
    values: dict[str, float] = {}
    for m in METRICS[kind]:
        v = a.get(m)
        if isinstance(v, int | float):
            values[m] = float(v)
    iso3 = a.get("ISO3")
    return DailyRecord(
        kind=kind,
        entity_id=str(a["portid"]),
        entity_name=str(a.get("portname") or a["portid"]),
        country_iso3=str(iso3) if iso3 else None,
        day=date.fromisoformat(str(a["date"])[:10]),
        metrics=values,
    )
