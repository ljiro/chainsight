from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from integrations.weather.open_meteo import OpenMeteoClient, OpenMeteoError
from services.alerting.freshness import is_stale


def location(lat: float, lon: float, wind: float = 12.0) -> dict[str, object]:
    return {
        "latitude": lat,
        "longitude": lon,
        "current_units": {"wind_speed_10m": "kn"},
        "current": {
            "time": "2026-11-26T04:45",
            "interval": 900,
            "temperature_2m": 9.5,
            "precipitation": 0.2,
            "wind_speed_10m": wind,
            "wind_direction_10m": 250,
            "wind_gusts_10m": 21.0,
        },
    }


def client_for(handler: httpx.MockTransport, **kw: object) -> OpenMeteoClient:
    sleeps: list[float] = []
    c = OpenMeteoClient(client=httpx.Client(transport=handler), sleep=sleeps.append, **kw)  # type: ignore[arg-type]
    c.sleeps = sleeps  # type: ignore[attr-defined]
    return c


def test_multi_location_request_and_parsing() -> None:
    seen: list[httpx.Request] = []

    def handle(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json=[location(51.9, 4.5), location(1.28, 103.85, wind=5)])

    points = client_for(httpx.MockTransport(handle)).current([(51.9167, 4.5), (1.2833, 103.85)])
    q = seen[0].url.params
    assert q["latitude"] == "51.9167,1.2833" and q["longitude"] == "4.5000,103.8500"
    assert q["wind_speed_unit"] == "kn" and q["timezone"] == "GMT"
    assert "apikey" not in q
    assert [p.wind_speed_kn for p in points] == [12.0, 5.0]
    assert points[0].valid_at == datetime(2026, 11, 26, 4, 45, tzinfo=UTC)


def test_single_location_returns_object_not_list() -> None:
    c = client_for(httpx.MockTransport(lambda _: httpx.Response(200, json=location(10, 10))))
    assert len(c.current([(10, 10)])) == 1


def test_api_key_sent_only_when_configured() -> None:
    seen: list[httpx.Request] = []

    def handle(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json=location(10, 10))

    client_for(httpx.MockTransport(handle), api_key="k").current([(10, 10)])
    assert seen[0].url.params["apikey"] == "k"


def test_retries_rate_limits_then_succeeds() -> None:
    responses = [
        httpx.Response(429, headers={"Retry-After": "3"}),
        httpx.Response(503),
        httpx.Response(200, json=location(10, 10)),
    ]
    c = client_for(httpx.MockTransport(lambda _: responses.pop(0)))
    assert len(c.current([(10, 10)])) == 1
    assert c.sleeps == [3.0, 6.0]  # type: ignore[attr-defined]


def test_client_errors_are_not_retried() -> None:
    calls = []

    def handle(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        return httpx.Response(400, json={"error": True, "reason": "Latitude must be in range"})

    with pytest.raises(OpenMeteoError, match="Latitude must be in range"):
        client_for(httpx.MockTransport(handle)).current([(99, 10)])
    assert len(calls) == 1


def test_unexpected_shape_is_an_error() -> None:
    c = client_for(httpx.MockTransport(lambda _: httpx.Response(200, json=[{"oops": 1}])))
    with pytest.raises(OpenMeteoError, match="unexpected response shape"):
        c.current([(10, 10)])


def test_location_count_mismatch_is_an_error() -> None:
    c = client_for(httpx.MockTransport(lambda _: httpx.Response(200, json=[location(1, 1)])))
    with pytest.raises(OpenMeteoError, match="expected 2"):
        c.current([(1, 1), (2, 2)])


def test_is_stale() -> None:
    now = datetime(2026, 11, 26, tzinfo=UTC)
    assert is_stale(None, 60, now)
    assert not is_stale(now - timedelta(seconds=119), 60, now)
    assert is_stale(now - timedelta(seconds=121), 60, now)
