from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from integrations.carriers.base import ResilientHttp, TokenBucket
from integrations.carriers.dcsa import (
    DcsaAuth,
    DcsaCarrierConfig,
    DcsaTntAdapter,
    event_fingerprint,
    normalize_dcsa_event,
    normalize_dcsa_payload,
)
from integrations.carriers.demo import DemoCarrierAdapter, demo_container_for, journey_for
from integrations.carriers.models import (
    CallRecord,
    CarrierAuthError,
    CarrierRateLimited,
    CarrierResponseInvalid,
    ShipmentNotFound,
    TrackingReference,
)
from integrations.carriers.references import (
    detect_reference_type,
    is_valid_container_number,
    iso6346_check_digit,
    normalize_reference,
)

NOW = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)


# ---- ISO 6346 ---------------------------------------------------------------------------


@pytest.mark.parametrize("number", ["CSQU3054383", "MSKU9070323"])
def test_valid_container_numbers(number: str) -> None:
    assert is_valid_container_number(number)


@pytest.mark.parametrize("number", ["CSQU3054384", "CSQU305438", "1SQU3054383", "CSQX3054383"])
def test_invalid_container_numbers(number: str) -> None:
    assert not is_valid_container_number(number)


def test_check_digit_and_normalization() -> None:
    assert iso6346_check_digit("CSQU305438") == 3
    assert normalize_reference(" csqu 305438-3 ") == "CSQU3054383"
    assert detect_reference_type("CSQU3054383") == "container"
    assert detect_reference_type("DEMO12345") == "booking"


# ---- DCSA normalization ------------------------------------------------------------------


def dcsa(**over: Any) -> dict[str, Any]:
    base = {
        "eventID": "e1",
        "eventType": "EQUIPMENT",
        "eventClassifierCode": "ACT",
        "eventDateTime": "2026-11-20T08:00:00+08:00",
        "eventCreatedDateTime": "2026-11-20T08:05:00Z",
        "equipmentEventTypeCode": "LOAD",
        "equipmentReference": "csqu3054383",
        "eventLocation": {"UNLocationCode": "sgsin", "locationName": "Singapore"},
        "transportCall": {
            "modeOfTransport": "VESSEL",
            "vessel": {"vesselIMONumber": "9811000", "vesselName": "EVER ACE"},
            "carrierExportVoyageNumber": "123E",
        },
    }
    return base | over


def test_equipment_event_maps_all_fields() -> None:
    ev, problem = normalize_dcsa_event(dcsa())
    assert problem is None and ev is not None
    assert (ev.event_type, ev.event_code, ev.classifier) == ("equipment", "LOAD", "ACTUAL")
    assert ev.event_at == datetime(2026, 11, 20, 0, 0, tzinfo=UTC)  # converted to UTC
    assert (ev.location_unlocode, ev.container_number) == ("SGSIN", "CSQU3054383")
    assert (ev.vessel_imo, ev.vessel_name, ev.voyage_number) == (9811000, "EVER ACE", "123E")
    assert ev.source_event_id == "e1" and ev.description == "Loaded on vessel"


def test_transport_event_location_from_transport_call() -> None:
    ev, _ = normalize_dcsa_event(
        dcsa(eventType="TRANSPORT", transportEventTypeCode="ARRI", eventClassifierCode="EST",
             eventLocation=None,
             transportCall={"location": {"UNLocationCode": "NLRTM"}, "vessel": {}})
    )  # fmt: skip
    assert ev is not None and (ev.event_code, ev.classifier, ev.location_unlocode) == (
        "ARRI",
        "ESTIMATED",
        "NLRTM",
    )


def test_unknown_codes_are_kept_raw_and_reported() -> None:
    ev, problem = normalize_dcsa_event(dcsa(equipmentEventTypeCode="XYZW"))
    assert ev is not None and ev.event_code == "UNKNOWN" and ev.raw_code == "XYZW"
    assert problem == "EQUIPMENT:XYZW"


@pytest.mark.parametrize(
    ("over", "problem"),
    [
        ({"eventClassifierCode": "REQ"}, "classifier:REQ"),
        ({"eventType": "MYSTERY"}, "eventType:MYSTERY"),
        ({"eventDateTime": "yesterday"}, "invalid:eventDateTime"),
        ({"eventDateTime": "2026-11-20T08:00:00"}, "invalid:eventDateTime"),  # no timezone
    ],
)
def test_events_that_cannot_be_used_are_skipped(over: dict[str, Any], problem: str) -> None:
    ev, reported = normalize_dcsa_event(dcsa(**over))
    assert ev is None and reported == problem


def test_payload_validation() -> None:
    events, unmapped, skipped = normalize_dcsa_payload(
        {"events": [dcsa(), "junk", dcsa(eventType="?")]}
    )
    assert len(events) == 1 and skipped == 2 and unmapped == ["eventType:?"]
    with pytest.raises(CarrierResponseInvalid):
        normalize_dcsa_payload({"error": "nope"})


def test_fingerprint_is_stable_and_discriminating() -> None:
    a, _ = normalize_dcsa_event(dcsa(eventID=None))
    b, _ = normalize_dcsa_event(dcsa(eventID=None, eventClassifierCode="EST"))
    assert a and b and event_fingerprint(a) == event_fingerprint(a) != event_fingerprint(b)


# ---- HTTP plumbing -----------------------------------------------------------------------


async def no_sleep(_: float) -> None:
    return None


def http_with(responses: list[httpx.Response]) -> ResilientHttp:
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: responses.pop(0)))
    return ResilientHttp(client, sleep=no_sleep)


async def test_retries_then_succeeds_and_records_calls() -> None:
    calls: list[CallRecord] = []
    http = http_with(
        [
            httpx.Response(503),
            httpx.Response(429, headers={"Retry-After": "2"}),
            httpx.Response(200, json=[1]),
        ]
    )
    assert await http.get_json("https://x/events", operation="track_shipment", calls=calls) == [1]
    assert [c.outcome for c in calls] == ["failure", "rate_limited", "success"]
    assert [c.http_status for c in calls] == [503, 429, 200]


@pytest.mark.parametrize(
    ("status", "exc"),
    [(401, CarrierAuthError), (403, CarrierAuthError), (404, ShipmentNotFound)],
)
async def test_non_retryable_errors(status: int, exc: type[Exception]) -> None:
    calls: list[CallRecord] = []
    with pytest.raises(exc):
        await http_with([httpx.Response(status)]).get_json("https://x", operation="t", calls=calls)
    assert len(calls) == 1


async def test_persistent_rate_limit_raises() -> None:
    with pytest.raises(CarrierRateLimited):
        await http_with([httpx.Response(429)] * 3).get_json("https://x", operation="t", calls=[])


async def test_token_bucket_waits_when_empty() -> None:
    t = [0.0]
    waits: list[float] = []

    async def sleep(d: float) -> None:
        waits.append(d)
        t[0] += d

    bucket = TokenBucket(2.0, burst=1, clock=lambda: t[0], sleep=sleep)
    await bucket.acquire()
    await bucket.acquire()
    assert waits == [pytest.approx(0.5)]


async def test_dcsa_adapter_query_auth_and_not_found() -> None:
    seen: list[httpx.Request] = []

    def handle(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json=[] if "EMPTY" in str(req.url) else [dcsa()])

    cfg = DcsaCarrierConfig(
        base_url="https://carrier.test/tnt/v2/",
        auth=DcsaAuth(type="api_key", header="Consumer-Key", api_key="k"),
    )
    adapter = DcsaTntAdapter(
        "maersk", cfg, client=httpx.AsyncClient(transport=httpx.MockTransport(handle))
    )
    result = await adapter.track(TrackingReference(reference_type="booking", value="ABC123"))
    assert seen[0].url.path == "/tnt/v2/events"
    assert seen[0].url.params["carrierBookingReference"] == "ABC123"
    assert seen[0].headers["Consumer-Key"] == "k"
    assert len(result.events) == 1 and result.calls[0].outcome == "success"
    with pytest.raises(ShipmentNotFound):
        await adapter.track(TrackingReference(reference_type="booking", value="EMPTY1"))


async def test_dcsa_adapter_without_credentials_needs_auth() -> None:
    adapter = DcsaTntAdapter(
        "msc", DcsaCarrierConfig(base_url="https://x", auth=DcsaAuth(type="api_key"))
    )
    health = await adapter.health_check()
    assert health.status == "authentication_required"
    with pytest.raises(CarrierAuthError):
        await adapter.track_shipment(TrackingReference(reference_type="booking", value="ABC123"))


def test_dcsa_config_repr_hides_secrets() -> None:
    cfg = DcsaCarrierConfig(
        base_url="https://x", auth=DcsaAuth(type="api_key", api_key="super-secret")
    )
    assert "super-secret" not in repr(cfg) and "super-secret" not in cfg.model_dump_json()


# ---- Demo carrier ------------------------------------------------------------------------


async def test_demo_refuses_real_references() -> None:
    adapter = DemoCarrierAdapter(clock=lambda: NOW)
    for ref in [TrackingReference(reference_type="booking", value="MAEU123456789"),
                TrackingReference(reference_type="container", value="CSQU3054383")]:  # fmt: skip
        with pytest.raises(ShipmentNotFound, match="never generates data for real references"):
            await adapter.track_shipment(ref)


async def test_demo_journey_is_deterministic_valid_and_synthetic() -> None:
    adapter = DemoCarrierAdapter(clock=lambda: NOW)
    ref = TrackingReference(reference_type="booking", value="DEMO100001")
    a, b = await adapter.track(ref), await adapter.track(ref)
    assert [e.model_dump() for e in a.events] == [e.model_dump() for e in b.events]
    assert is_valid_container_number(demo_container_for("DEMO100001"))
    assert all((e.vessel_name or "SYNTHETIC").startswith("SYNTHETIC") for e in a.events)
    assert all(e.vessel_imo is None for e in a.events)
    assert not a.unmapped_codes and a.skipped_events == 0
    # No actual event may be in the future.
    assert all(e.event_at <= NOW for e in a.events if e.classifier == "ACTUAL")


async def test_demo_estimates_appear_only_while_in_transit() -> None:
    adapter = DemoCarrierAdapter(clock=lambda: NOW)
    for i in range(40):
        ref = f"DEMO{i:06d}"
        j = journey_for(ref, NOW)
        result = await adapter.track(TrackingReference(reference_type="booking", value=ref))
        ests = [e for e in result.events if e.classifier == "ESTIMATED"]
        arrived = any(
            e.classifier == "ACTUAL"
            and e.event_code == "ARRI"
            and e.location_unlocode == j.destination
            for e in result.events
        )
        assert not (arrived and ests), ref  # once arrived, no estimate
        assert all(e.event_at > NOW for e in ests)


def test_demo_routes_cover_stages() -> None:
    stages = {journey_for(f"DEMO{i:06d}", NOW).delay_h for i in range(200)}
    assert {0, 80} <= stages  # some on time, some badly delayed
    assert journey_for("DEMO1", NOW).booked_at < NOW - timedelta(days=1)
