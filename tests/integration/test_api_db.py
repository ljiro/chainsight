from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

from chainsight_api.health import expected_migration_head, get_readiness_probes, probe_database
from chainsight_api.main import create_app


@pytest.fixture
def client(migrated_db: Engine, auth_headers: dict[str, str]) -> Iterator[TestClient]:
    with TestClient(create_app(), headers=auth_headers) as c:
        yield c


def test_readiness_database_component(client: TestClient) -> None:
    client.app.dependency_overrides[get_readiness_probes] = lambda: [probe_database]  # type: ignore[attr-defined]
    r = client.get("/api/v1/health/ready")
    assert r.status_code == 200, r.text
    db = r.json()["components"][0]
    assert db["status"] == "ok", db
    assert db["detail"]["postgis"]
    assert db["detail"]["migration_revision"] == expected_migration_head()


def test_list_data_sources(client: TestClient) -> None:
    r = client.get("/api/v1/data-sources")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 10
    ais = next(i for i in body["items"] if i["key"] == "aisstream")
    assert ais["data_mode"] == "real_time"
    assert ais["status"] == "not_configured"
    assert ais["freshness"] == "never"
    assert "last_error_message" not in ais


def test_filter_data_sources_by_category(client: TestClient) -> None:
    r = client.get("/api/v1/data-sources", params={"category": "weather"})
    assert [i["key"] for i in r.json()["items"]] == ["open-meteo"]


def test_list_carriers_with_integration_status(client: TestClient) -> None:
    r = client.get("/api/v1/carriers")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 9
    names = [c["name"] for c in body["items"]]
    assert names == sorted(names)
    # Startup syncs statuses honestly: without credentials nothing is "connected".
    statuses = {c["code"]: c["integrations"][0] for c in body["items"]}
    assert {i["status"] for i in statuses.values()} <= {
        "authentication_required",
        "unavailable",
        "demo_data_only",
    }
    for code in ("maersk", "msc", "cma-cgm", "hapag-lloyd", "cosco", "evergreen", "one"):
        assert statuses[code]["status"] == "authentication_required", code
        assert "CARRIER_DCSA_CONFIG" in statuses[code]["status_reason"]
        assert statuses[code]["last_success_at"] is None
    assert statuses["dhl"]["status"] == "unavailable"
    assert statuses["demo"]["status"] == "demo_data_only"


def test_carriers_filter_sort_and_paginate(client: TestClient) -> None:
    r = client.get("/api/v1/carriers", params={"carrier_type": "logistics_provider"})
    assert [c["code"] for c in r.json()["items"]] == ["dhl"]

    r = client.get("/api/v1/carriers", params={"sort": "-code", "limit": 2, "offset": 1})
    body = r.json()
    assert body["total"] == 9 and body["limit"] == 2 and body["offset"] == 1
    assert [c["code"] for c in body["items"]] == ["msc", "maersk"]


def test_invalid_sort_rejected(client: TestClient) -> None:
    r = client.get("/api/v1/carriers", params={"sort": "scac; drop table"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"
