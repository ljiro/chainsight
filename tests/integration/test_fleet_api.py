from __future__ import annotations

import asyncio
import io
import json
from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any

import pytest
import redis
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.websockets import WebSocketDisconnect

from chainsight_api.main import create_app
from core.timeutils import utcnow
from integrations.ais.providers import RawAisMessage
from integrations.ports.unlocode import iter_seaports
from services.ingestion.ais_pipeline import LIVE_CHANNEL, AisPipeline
from services.ingestion.port_registry import import_ports

NOW = utcnow().replace(microsecond=0)
CSV = """Change,Country,Location,Name,NameWoDiacritics,Subdivision,Status,Function,Date,IATA,Coordinates,Remarks
,ZX,APA,Api Port,Api Port,,AI,1-------,2401,,2000N 03000E,
"""
PORT = (20.0, 30.0)
TANKER, CARGO, PACIFIC = 233000001, 233000002, 233000003


def msg(
    mmsi: int, t: datetime, lat: float, lon: float, sog: float, nav: int = 0, cog: float = 90
) -> RawAisMessage:
    return RawAisMessage(
        {
            "MessageType": "PositionReport",
            "MetaData": {"MMSI": mmsi, "ShipName": f"API {mmsi}", "time_utc": t.strftime("%Y-%m-%d %H:%M:%S.%f +0000 UTC")},
            "Message": {"PositionReport": {"Latitude": lat, "Longitude": lon, "Sog": sog, "Cog": cog,
                                           "TrueHeading": 90, "NavigationalStatus": nav}},
        },
        t,
    )  # fmt: skip


def static(mmsi: int, t: datetime, name: str, ship_type: int) -> RawAisMessage:
    return RawAisMessage(
        {
            "MessageType": "ShipStaticData",
            "MetaData": {"MMSI": mmsi, "time_utc": t.strftime("%Y-%m-%d %H:%M:%S +0000 UTC")},
            "Message": {"ShipStaticData": {"Name": name, "Type": ship_type, "ImoNumber": 0}},
        },
        t,
    )


@pytest.fixture(scope="module")
def seeded(migrated_db: Engine, database_url: str) -> dict[str, Any]:
    import_ports(migrated_db, iter_seaports(io.StringIO(CSV)), radius_m=8000)

    async def seed() -> None:
        engine = create_async_engine(database_url)
        p = AisPipeline(async_sessionmaker(engine, expire_on_commit=False),
                        source_key="ais-simulator", receiver_type="synthetic")  # fmt: skip
        await p.start()
        # Tanker: 2 h of track with a 90-minute AIS gap, ending moored inside the port.
        p.ingest(static(TANKER, NOW - timedelta(hours=3), "MSC API TANKER", 80))
        for k in range(30):
            t = NOW - timedelta(hours=3) + timedelta(minutes=2 * k)
            p.ingest(msg(TANKER, t, 19.0 + k * 0.01, 29.0 + k * 0.01, 12.0))
        p.ingest(msg(TANKER, NOW - timedelta(minutes=30), 19.9, 29.9, 8.0, cog=45))
        p.ingest(msg(TANKER, NOW - timedelta(minutes=2), PORT[0], PORT[1], 0.0, nav=5))
        # Cargo vessel underway, heading straight at the port from the south-west.
        p.ingest(static(CARGO, NOW - timedelta(minutes=5), "API CARGO", 70))
        p.ingest(msg(CARGO, NOW - timedelta(minutes=1), 19.8, 29.8, 14.0, cog=43))
        # A vessel near the antimeridian, last seen 3 h ago (stale).
        p.ingest(msg(PACIFIC, NOW - timedelta(hours=3), 10.0, 179.5, 15.0))
        await p.flush()
        await engine.dispose()

    asyncio.run(seed())
    with migrated_db.connect() as conn:
        ids = dict(conn.execute(sa.text("SELECT mmsi, id FROM vessels WHERE mmsi IN (:a, :b, :c)"),
                                {"a": TANKER, "b": CARGO, "c": PACIFIC}).all())  # fmt: skip
        port_id = conn.execute(
            sa.text("SELECT id FROM ports WHERE unlocode = 'ZXAPA'")
        ).scalar_one()
    return {"ids": ids, "port_id": port_id}


@pytest.fixture
def client(seeded: dict[str, Any]) -> Iterator[TestClient]:
    with TestClient(create_app()) as c:
        yield c


def test_positions_geojson_carries_provenance(client: TestClient, seeded: dict[str, Any]) -> None:
    r = client.get("/api/v1/fleet/positions", params={"bbox": "28,18,32,22"})
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "FeatureCollection"
    by_mmsi = {f["properties"]["mmsi"]: f for f in body["features"]}
    assert set(by_mmsi) == {TANKER, CARGO}
    tanker = by_mmsi[TANKER]["properties"]
    assert tanker["data_mode"] == "synthetic" and tanker["source"] == "ais-simulator"
    assert tanker["freshness"] == "live" and tanker["vessel_class"] == "tanker"
    assert tanker["carrier"] == "msc"  # inferred from the name prefix
    assert by_mmsi[TANKER]["geometry"]["coordinates"] == pytest.approx([PORT[1], PORT[0]])
    assert body["meta"]["by_data_mode"] == {"synthetic": 2}


def test_positions_bbox_across_antimeridian(client: TestClient) -> None:
    r = client.get("/api/v1/fleet/positions", params={"bbox": "170,0,-170,20"})
    feats = r.json()["features"]
    assert [f["properties"]["mmsi"] for f in feats] == [PACIFIC]
    assert feats[0]["properties"]["freshness"] == "stale"


@pytest.mark.parametrize("bbox", ["1,2,3", "a,b,c,d", "0,50,10,40", "0,0,200,10"])
def test_invalid_bbox_rejected(client: TestClient, bbox: str) -> None:
    r = client.get("/api/v1/fleet/positions", params={"bbox": bbox})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_bbox"


def test_filters_by_class_and_search(client: TestClient) -> None:
    r = client.get("/api/v1/vessels", params={"vessel_class": "tanker", "q": "API"})
    assert [v["mmsi"] for v in r.json()["items"]] == [TANKER]
    r = client.get("/api/v1/vessels", params={"q": str(CARGO)})
    assert [v["name"] for v in r.json()["items"]] == ["API CARGO"]


def test_vessel_detail(client: TestClient, seeded: dict[str, Any]) -> None:
    vid = seeded["ids"][TANKER]
    r = client.get(f"/api/v1/vessels/{vid}")
    assert r.status_code == 200
    v = r.json()
    assert v["vessel_class_source"] == "ais_ship_type"
    assert {(i["scheme"], i["value"]) for i in v["identifiers"]} >= {
        ("mmsi", str(TANKER)),
        ("name", "MSC API TANKER"),
    }
    assert v["carrier_history"][0]["method"] == "name_heuristic"
    assert v["position"]["nav_status_label"] == "Moored"
    assert v["open_port_call"]["port"]["unlocode"] == "ZXAPA"


def test_unknown_vessel_404(client: TestClient) -> None:
    r = client.get("/api/v1/vessels/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404 and r.json()["error"]["code"] == "vessel_not_found"


def test_track_with_gap_and_downsampling(client: TestClient, seeded: dict[str, Any]) -> None:
    vid = seeded["ids"][TANKER]
    r = client.get(
        f"/api/v1/vessels/{vid}/track", params={"start": (NOW - timedelta(hours=4)).isoformat()}
    )
    t = r.json()
    assert r.status_code == 200 and t["total_points"] == 32
    assert len(t["gaps"]) == 1 and t["gaps"][0]["duration_seconds"] == pytest.approx(
        5520
    )  # 2:02 -> 0:30 before now
    assert len(t["geometry"]["coordinates"]) == 2  # two continuous segments
    assert t["sources"] == [{"source": "ais-simulator", "data_mode": "synthetic"}]

    small = client.get(
        f"/api/v1/vessels/{vid}/track",
        params={"start": (NOW - timedelta(hours=4)).isoformat(), "max_points": 10},
    ).json()
    assert small["returned_points"] <= 11 and small["points"][-1]["t"] == t["points"][-1]["t"]


def test_track_validation(client: TestClient, seeded: dict[str, Any]) -> None:
    vid = seeded["ids"][TANKER]
    r = client.get(
        f"/api/v1/vessels/{vid}/track",
        params={"start": NOW.isoformat(), "end": (NOW - timedelta(hours=1)).isoformat()},
    )
    assert r.json()["error"]["code"] == "invalid_time_range"
    r = client.get(
        f"/api/v1/vessels/{vid}/track", params={"start": (NOW - timedelta(days=40)).isoformat()}
    )
    assert r.json()["error"]["code"] == "time_range_too_large"


def test_port_calls_and_port_detail(client: TestClient, seeded: dict[str, Any]) -> None:
    vid = seeded["ids"][TANKER]
    calls = client.get(f"/api/v1/vessels/{vid}/port-calls").json()
    assert calls["total"] == 1
    call = calls["items"][0]
    assert call["port"]["unlocode"] == "ZXAPA" and call["actual_departure_at"] is None
    assert call["actual_arrival_at"] is not None  # observed arriving (vessel was tracked before)

    port = client.get(f"/api/v1/ports/{seeded['port_id']}").json()
    assert port["vessels_in_port"] == 1 and port["geofence_radius_m"] == 8000
    assert [c["vessel_name"] for c in port["in_port"]] == ["MSC API TANKER"]
    assert port["weather"] is None  # nothing collected yet: shown as absent, never invented

    listed = client.get("/api/v1/ports", params={"bbox": "29,19,31,21"}).json()
    assert [p["unlocode"] for p in listed] == ["ZXAPA"]


def test_fleet_summary_and_approaching(client: TestClient) -> None:
    s = client.get("/api/v1/fleet/summary").json()
    assert s["vessels_live"] >= 2 and s["in_port"] >= 1
    assert s["by_data_mode"].get("synthetic", 0) >= 2
    assert s["carrier_associations"].get("msc", 0) >= 1

    approaching = client.get("/api/v1/fleet/approaching", params={"within_nm": 30}).json()
    assert [(a["vessel"]["mmsi"], a["port"]["unlocode"]) for a in approaching] == [(CARGO, "ZXAPA")]
    assert 0 < approaching[0]["distance_nm"] < 30


def test_alerts_endpoint(client: TestClient, migrated_db: Engine) -> None:
    with migrated_db.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO system_alerts (severity, category, title, dedupe_key) VALUES ('critical', 'test', 'API alert', 'api-test')"
            )
        )
    r = client.get("/api/v1/alerts", params={"severity": "critical"}).json()
    assert [a["title"] for a in r["items"]] == ["API alert"]
    with migrated_db.begin() as conn:
        conn.execute(sa.text("DELETE FROM system_alerts WHERE dedupe_key = 'api-test'"))


def test_websocket_streams_filtered_positions(client: TestClient) -> None:
    r = redis.Redis.from_url("redis://localhost:6379/0")
    try:
        r.ping()
    except redis.ConnectionError:
        pytest.skip("Redis not available")
    message = {
        "source": "ais-simulator",
        "data_mode": "synthetic",
        "positions": [
            {"vessel_id": "a", "lat": 20.0, "lon": 30.0},
            {"vessel_id": "b", "lat": -40.0, "lon": 100.0},
        ],
    }
    with client.websocket_connect("/api/v1/ws/positions?bbox=28,18,32,22") as ws:
        assert ws.receive_json()["type"] == "hello"
        for _ in range(20):  # subscription is async; publish until the first frame arrives
            r.publish(LIVE_CHANNEL, json.dumps(message))
            try:
                frame = ws.receive_json(mode="text")
                break
            except Exception:  # pragma: no cover
                continue
        assert frame["type"] == "positions" and frame["data_mode"] == "synthetic"
        assert [p["vessel_id"] for p in frame["positions"]] == ["a"]


def test_websocket_rejects_foreign_origin(client: TestClient) -> None:
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect(
            "/api/v1/ws/positions", headers={"origin": "https://evil.example"}
        ) as ws,
    ):
        ws.receive_json()
