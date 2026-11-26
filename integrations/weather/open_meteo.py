"""Open-Meteo forecast API client: current conditions for many coordinates per request.

Docs: https://open-meteo.com/en/docs (free for non-commercial use; data CC BY 4.0).
A multi-location request passes comma-separated latitude/longitude lists and returns a
JSON list (a single location returns a single object).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

import httpx
from pydantic import BaseModel, ValidationError

from core.logging_config import get_logger

log = get_logger(__name__)

CURRENT_VARIABLES = (
    "temperature_2m",
    "precipitation",
    "wind_speed_10m",
    "wind_direction_10m",
    "wind_gusts_10m",
)
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


class WeatherPoint(BaseModel):
    latitude: float
    longitude: float
    valid_at: datetime
    temperature_c: float | None
    precipitation_mm: float | None
    wind_speed_kn: float | None
    wind_direction_deg: float | None
    wind_gusts_kn: float | None


class _Current(BaseModel):
    time: str
    temperature_2m: float | None = None
    precipitation: float | None = None
    wind_speed_10m: float | None = None
    wind_direction_10m: float | None = None
    wind_gusts_10m: float | None = None


class _Location(BaseModel):
    latitude: float
    longitude: float
    current: _Current


class OpenMeteoError(Exception):
    pass


class OpenMeteoClient:
    def __init__(
        self,
        base_url: str = "https://api.open-meteo.com/v1/forecast",
        *,
        api_key: str | None = None,
        client: httpx.Client | None = None,
        retries: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.base_url = base_url
        self._api_key = api_key
        self._client = client or httpx.Client(timeout=20.0)
        self.retries = retries
        self._sleep = sleep

    def current(self, points: Sequence[tuple[float, float]]) -> list[WeatherPoint]:
        """Current conditions for (lat, lon) points, returned in the same order."""
        if not points:
            return []
        params: dict[str, str] = {
            "latitude": ",".join(f"{lat:.4f}" for lat, _ in points),
            "longitude": ",".join(f"{lon:.4f}" for _, lon in points),
            "current": ",".join(CURRENT_VARIABLES),
            "wind_speed_unit": "kn",
            "timezone": "GMT",
        }
        if self._api_key:
            params["apikey"] = self._api_key
        payload = self._get(params)
        items = payload if isinstance(payload, list) else [payload]
        if len(items) != len(points):
            raise OpenMeteoError(f"expected {len(points)} locations, got {len(items)}")
        try:
            locations = [_Location.model_validate(item) for item in items]
        except ValidationError as exc:
            raise OpenMeteoError(f"unexpected response shape: {exc.error_count()} errors") from exc
        return [
            WeatherPoint(
                latitude=loc.latitude,
                longitude=loc.longitude,
                # timezone=GMT returns naive ISO times in UTC.
                valid_at=datetime.fromisoformat(loc.current.time).replace(tzinfo=UTC),
                temperature_c=loc.current.temperature_2m,
                precipitation_mm=loc.current.precipitation,
                wind_speed_kn=loc.current.wind_speed_10m,
                wind_direction_deg=loc.current.wind_direction_10m,
                wind_gusts_kn=loc.current.wind_gusts_10m,
            )
            for loc in locations
        ]

    def _get(self, params: dict[str, str]) -> object:
        delay = 1.0
        for attempt in range(1, self.retries + 1):
            try:
                resp = self._client.get(self.base_url, params=params)
            except httpx.TransportError as exc:
                if attempt == self.retries:
                    raise OpenMeteoError(f"transport error: {type(exc).__name__}") from exc
            else:
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code not in RETRY_STATUSES or attempt == self.retries:
                    reason = (
                        resp.json().get("reason")
                        if resp.headers.get("content-type", "").startswith("application/json")
                        else None
                    )
                    raise OpenMeteoError(f"HTTP {resp.status_code}: {reason or 'request failed'}")
                retry_after = resp.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    delay = float(retry_after)
            log.warning("open_meteo_retry", attempt=attempt, delay_s=delay)
            self._sleep(delay)
            delay *= 2
        raise OpenMeteoError("unreachable")  # pragma: no cover
