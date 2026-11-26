from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from chainsight_api.main import create_app
from chainsight_api.routers.shipments import get_registry
from integrations.carriers.dcsa import DcsaAuth, DcsaCarrierConfig, DcsaTntAdapter
from integrations.carriers.demo import DemoCarrierAdapter, journey_for
from integrations.carriers.models import TrackingReference
from services.shipment_tracking.registry import CarrierRegistry, sync_integration_statuses
from services.shipment_tracking.service import track

IMO = 9811000  # a tracked vessel will be created with this IMO


def q(engine: Engine, sql: str, **params: Any) -> list[Any]:
    with engine.connect() as conn:
        return list(conn.execute(sa.text(sql), params))


@pytest.fixture(scope="module")
def tracked_vessel(migrated_db: Engine) -> str:
    with migrated_db.begin() as conn:
        return str(
            conn.execute(
                sa.text(
                    "INSERT INTO vessels (mmsi, imo, name, last_seen_at, last_location, last_sog_knots, "
                    "last_position_source_id) SELECT 563000001, :imo, 'EVER TEST', now(), "
                    "ST_MakePoint(103.8, 1.2)::geography, 14.0, id FROM data_sources WHERE key = 'aisstream' "
                    "ON CONFLICT (imo) DO UPDATE SET name = EXCLUDED.name RETURNING id"
                ),
                {"imo": IMO},
            ).scalar_one()
        )


@pytest.fixture
def client(migrated_db: Engine) -> Iterator[TestClient]:
    with TestClient(create_app()) as c:
        yield c


def test_demo_tracking_end_to_end(client: TestClient) -> None:
    r = client.post("/api/v1/shipments/track", json={"carrier": "demo", "reference": "demo-000123"})
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["reference_value"] == "DEMO000123" and s["reference_type"] == "booking"
    assert s["provenance"] == {"source": "carrier-demo", "data_mode": "synthetic"}
    assert s["events"] and {e["classifier"] for e in s["events"]} >= {"PLANNED"}
    assert s["containers"] and s["containers"][0].startswith("ZZZU")
    assert s["status"] != "unknown" and s["planned_arrival_at"]
    assert all(
        leg["carrier_vessel_name"].startswith("SYNTHETIC") and leg["vessel_id"] is None
        for leg in s["legs"]
    )
    j = journey_for("DEMO000123", datetime.now(UTC))
    assert s["destination"]["unlocode"] == j.destination

    again = client.post(
        "/api/v1/shipments/track", json={"carrier": "demo", "reference": "DEMO000123"}
    ).json()
    assert again["id"] == s["id"] and len(again["events"]) == len(s["events"])  # no duplicates


def test_demo_never_answers_real_references(client: TestClient) -> None:
    r = client.post("/api/v1/shipments/track", json={"carrier": "demo", "reference": "CSQU3054383"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "reference_not_found"


def test_carrier_without_credentials_reports_status(client: TestClient) -> None:
    r = client.post(
        "/api/v1/shipments/track", json={"carrier": "maersk", "reference": "MAEU123456789"}
    )
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "carrier_integration_unavailable"
    assert err["details"]["status"] == "authentication_required"
    assert "CARRIER_DCSA_CONFIG" in err["details"]["reason"]

    r = client.post("/api/v1/shipments/track", json={"carrier": "dhl", "reference": "1234567890"})
    assert r.json()["error"]["details"]["status"] == "unavailable"
    assert (
        client.post(
            "/api/v1/shipments/track", json={"carrier": "nope", "reference": "ABC123"}
        ).status_code
        == 404
    )


async def test_new_estimates_append_and_baseline_is_write_once(
    database_url: str, migrated_db: Engine
) -> None:
    # Pick a demo reference that is in transit at T1 so it carries an estimate.
    t1 = datetime(2026, 11, 26, 12, tzinfo=UTC)
    ref_value = next(
        f"DEMO9{i:05d}" for i in range(500)
        if (j := journey_for(f"DEMO9{i:05d}", t1)).delay_h >= 36 and j.total_days >= 10
        and j.booked_at + timedelta(days=3) < t1
        and t1 + timedelta(days=4) < j.booked_at + timedelta(days=2 + j.total_days)
    )  # fmt: skip
    engine = create_async_engine(database_url)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    ref = TrackingReference(reference_type="booking", value=ref_value)
    try:
        for t in (t1, t1 + timedelta(days=4)):
            registry = CarrierRegistry(adapters={"demo": DemoCarrierAdapter(clock=lambda t=t: t)})
            async with sm() as session, session.begin():
                await track(
                    session, registry, carrier_code="demo", reference=ref, threshold_hours=12
                )
    finally:
        await engine.dispose()

    rows = q(
        migrated_db,
        "SELECT id, planned_arrival_at, delay_status, delay_hours FROM shipments WHERE reference_value = :r",
        r=ref_value,
    )
    shipment = rows[0]
    ests = q(
        migrated_db,
        "SELECT event_created_at FROM shipment_events WHERE shipment_id = :s AND classifier = 'ESTIMATED' ORDER BY event_created_at",
        s=shipment.id,
    )
    assert len(ests) == 2  # the re-issued estimate is a new row; the earlier one is kept
    assert shipment.delay_status == "delayed" and float(shipment.delay_hours) > 12

    with migrated_db.begin() as conn:  # a later re-plan must not move the baseline
        conn.execute(
            sa.text("UPDATE shipments SET planned_arrival_at = planned_arrival_at WHERE id = :s"),
            {"s": shipment.id},
        )
    baseline = shipment.planned_arrival_at
    assert (
        q(migrated_db, "SELECT planned_arrival_at FROM shipments WHERE id = :s", s=shipment.id)[
            0
        ].planned_arrival_at
        == baseline
    )


def dcsa_events(container: str = "CSQU3054383") -> list[dict[str, Any]]:
    base = {"equipmentReference": container, "eventCreatedDateTime": "2026-11-01T00:00:00Z"}
    call = {"modeOfTransport": "VESSEL", "vessel": {"vesselIMONumber": str(IMO), "vesselName": "EVER TEST"},
            "carrierExportVoyageNumber": "123E"}  # fmt: skip
    return [
        base | {"eventID": "h1", "eventType": "EQUIPMENT", "eventClassifierCode": "ACT", "equipmentEventTypeCode": "LOAD",
                "eventDateTime": "2026-11-02T00:00:00Z", "eventLocation": {"UNLocationCode": "SGSIN"}, "transportCall": call},
        base | {"eventID": "h2", "eventType": "TRANSPORT", "eventClassifierCode": "PLN", "transportEventTypeCode": "ARRI",
                "eventDateTime": "2026-11-28T00:00:00Z", "eventLocation": {"UNLocationCode": "NLRTM"}, "transportCall": call},
        base | {"eventID": "h3", "eventType": "EQUIPMENT", "eventClassifierCode": "ACT", "equipmentEventTypeCode": "ZZZZ",
                "eventDateTime": "2026-11-03T00:00:00Z", "eventLocation": {"UNLocationCode": "SGSIN"}},
    ]  # fmt: skip


def test_dcsa_carrier_links_vessel_by_imo_and_logs_calls(
    client: TestClient, migrated_db: Engine, tracked_vessel: str
) -> None:
    status = {"code": 200}

    def handle(req: httpx.Request) -> httpx.Response:
        if status["code"] != 200:
            return httpx.Response(status["code"])
        return httpx.Response(200, json=dcsa_events(), headers={"x-request-id": "req-1"})

    adapter = DcsaTntAdapter(
        "hapag-lloyd",
        DcsaCarrierConfig(
            base_url="https://hlag.test/tnt/v2", auth=DcsaAuth(type="api_key", api_key="k")
        ),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    )
    registry = CarrierRegistry(adapters={"demo": DemoCarrierAdapter(), "hapag-lloyd": adapter})
    client.app.dependency_overrides[get_registry] = lambda: registry  # type: ignore[attr-defined]

    r = client.post(
        "/api/v1/shipments/track", json={"carrier": "hapag-lloyd", "reference": "CSQU3054383"}
    )
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["provenance"] == {"source": "carrier-hapag-lloyd", "data_mode": "real_time"}
    assert s["reference_type"] == "container" and s["status"] == "loaded"
    leg = s["legs"][0]
    assert leg["vessel_id"] == tracked_vessel and leg["carrier_vessel_imo"] == IMO
    assert leg["vessel_position"]["provenance"]["source"] == "aisstream"
    unknown = next(e for e in s["events"] if e["raw_code"] == "ZZZZ")
    assert unknown["event_code"] == "UNKNOWN"  # kept, not dropped or guessed

    logs = q(migrated_db, "SELECT l.outcome, l.http_status, l.provider_request_id FROM carrier_integration_logs l "
                          "JOIN carrier_integrations ci ON ci.id = l.carrier_integration_id JOIN carriers c ON c.id = ci.carrier_id "
                          "WHERE c.code = 'hapag-lloyd'")  # fmt: skip
    assert [(lg.outcome, lg.http_status, lg.provider_request_id) for lg in logs] == [
        ("success", 200, "req-1")
    ]

    status["code"] = 401
    r = client.post(
        "/api/v1/shipments/track", json={"carrier": "hapag-lloyd", "reference": "CSQU3054383"}
    )
    assert r.status_code == 502 and r.json()["error"]["code"] == "carrier_auth_failed"
    integ = q(
        migrated_db,
        "SELECT ci.status FROM carrier_integrations ci JOIN carriers c ON c.id = ci.carrier_id WHERE c.code = 'hapag-lloyd'",
    )
    assert integ[0].status == "authentication_required"
    assert len(q(migrated_db, "SELECT 1 FROM carrier_integration_logs l JOIN carrier_integrations ci ON ci.id = l.carrier_integration_id "
                              "JOIN carriers c ON c.id = ci.carrier_id WHERE c.code = 'hapag-lloyd'")) == 2  # failure logged too  # fmt: skip

    # Restore honest statuses for later tests.
    async def resync() -> None:
        from database.session import get_async_engine, get_sessionmaker

        async with get_sessionmaker()() as session, session.begin():
            await sync_integration_statuses(
                session, CarrierRegistry(adapters={"demo": DemoCarrierAdapter()})
            )
        await get_async_engine().dispose()

    asyncio.run(resync())


CSV_OK = """carrier_code,reference_type,reference_value,event_code,classifier,event_at,location_unlocode,container_number,vessel_name,vessel_imo,voyage_number,origin_unlocode,destination_unlocode
msc,booking,CSVBKG0001,GTIN,ACT,2026-11-01T08:00:00Z,SGSIN,MSKU9070323,,,,SGSIN,NLRTM
msc,booking,CSVBKG0001,LOAD,ACT,2026-11-02T08:00:00Z,SGSIN,MSKU9070323,EVER TEST,9811000,7E,,
msc,booking,CSVBKG0001,ARRI,PLN,2026-11-25T00:00:00Z,NLRTM,,EVER TEST,9811000,7E,,
msc,booking,CSVBKG0001,ARRI,ACT,2026-11-27T06:00:00Z,NLRTM,,EVER TEST,9811000,7E,,
cma-cgm,container,CSQU3054383,LOAD,PLANNED,2026-12-01T00:00:00+08:00,HKHKG,CSQU3054383,,,,,
"""


def test_csv_import_is_atomic_and_idempotent(
    client: TestClient, migrated_db: Engine, tracked_vessel: str
) -> None:
    bad = CSV_OK.replace("MSKU9070323", "MSKU9070324", 1).replace(
        "2026-12-01T00:00:00+08:00", "2026-12-01 00:00"
    )
    r = client.post("/api/v1/shipments/import", content=bad, headers={"Content-Type": "text/csv"})
    assert r.status_code == 422
    errors = r.json()["errors"]
    assert {(e["row"], e["field"]) for e in errors} == {(2, "container_number"), (6, "event_at")}
    assert not q(
        migrated_db, "SELECT 1 FROM shipments WHERE reference_value = 'CSVBKG0001'"
    )  # nothing imported

    r = client.post(
        "/api/v1/shipments/import", content=CSV_OK, headers={"Content-Type": "text/csv"}
    )
    assert r.status_code == 200, r.text
    report = r.json()
    assert report["ok"] and report["shipments_created"] == 2 and report["events_inserted"] == 5

    again = client.post(
        "/api/v1/shipments/import", content=CSV_OK, headers={"Content-Type": "text/csv"}
    ).json()
    assert (
        again["events_inserted"] == 0
        and again["events_duplicate"] == 5
        and again["shipments_updated"] == 2
    )

    msc = client.get("/api/v1/shipments", params={"q": "MSKU9070323"}).json()["items"]
    assert len(msc) == 1
    s = client.get(f"/api/v1/shipments/{msc[0]['id']}").json()
    assert s["provenance"] == {"source": "csv-import", "data_mode": "historical"}
    assert (s["status"], s["delay_status"], s["delay_hours"]) == (
        "in_transit",
        "arrived_late",
        54.0,
    )
    assert s["legs"][0]["vessel_id"] == tracked_vessel  # linked by reported IMO
    assert s["refreshable"] is False
    r = client.post(f"/api/v1/shipments/{s['id']}/refresh")
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_refreshable"


def test_csv_import_rejects_missing_columns(client: TestClient) -> None:
    r = client.post(
        "/api/v1/shipments/import",
        content="carrier_code,reference_value\nmsc,X\n",
        headers={"Content-Type": "text/csv"},
    )
    assert r.status_code == 422 and r.json()["errors"][0]["message"] == "missing required column(s)"


def test_summary_and_carrier_performance(client: TestClient) -> None:
    s = client.get("/api/v1/shipments/summary").json()
    assert (
        s["total"] >= 3
        and s["by_data_mode"].get("synthetic", 0) >= 1
        and s["by_data_mode"].get("historical", 0) >= 2
    )
    perf = {p["code"]: p for p in client.get("/api/v1/carriers/performance").json()}
    assert perf["msc"]["shipments_tracked"] >= 1 and perf["msc"]["arrivals"] >= 1
    assert perf["msc"]["on_time_rate"] == 0.0 and perf["msc"]["avg_arrival_delay_hours"] == 54.0
    assert perf["maersk"]["integration_status"] == "authentication_required"
    assert perf["dhl"]["integration_status"] == "unavailable"
    assert perf["demo"]["integration_status"] == "demo_data_only"
    assert list(perf)[-1] == "demo"  # demo carrier sorts last


def test_unknown_shipment_404(client: TestClient) -> None:
    r = client.get("/api/v1/shipments/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404 and r.json()["error"]["code"] == "shipment_not_found"
