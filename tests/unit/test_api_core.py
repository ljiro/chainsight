"""API behaviour that needs no database: liveness, readiness aggregation, error envelope."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from chainsight_api.health import get_readiness_probes, overall_status
from chainsight_api.main import create_app
from chainsight_api.routers.catalog import compute_freshness
from chainsight_api.schemas.health import ComponentHealth


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app()

    boom = APIRouter()

    @boom.get("/__boom")
    async def _boom() -> None:
        raise RuntimeError("secret internal detail")

    app.include_router(boom)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _probe(name: str, status: str):  # type: ignore[no-untyped-def]
    async def probe() -> ComponentHealth:
        return ComponentHealth(name=name, status=status)  # type: ignore[arg-type]

    return probe


def test_liveness(client: TestClient) -> None:
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "ChainSight"
    assert r.headers["X-Request-ID"]


def test_request_id_is_propagated_when_valid(client: TestClient) -> None:
    r = client.get("/api/v1/health", headers={"X-Request-ID": "abc-123"})
    assert r.headers["X-Request-ID"] == "abc-123"


def test_request_id_is_replaced_when_malicious(client: TestClient) -> None:
    r = client.get("/api/v1/health", headers={"X-Request-ID": "bad id\nInjected: 1"})
    assert r.headers["X-Request-ID"] != "bad id\nInjected: 1"
    assert len(r.headers["X-Request-ID"]) == 32


@pytest.mark.parametrize(
    ("states", "expected_overall", "expected_http"),
    [
        (["ok", "ok"], "ok", 200),
        (["ok", "degraded"], "degraded", 200),
        (["degraded", "error"], "error", 503),
    ],
)
def test_readiness_aggregation(
    client: TestClient, states: list[str], expected_overall: str, expected_http: int
) -> None:
    probes = [_probe(f"c{i}", s) for i, s in enumerate(states)]
    client.app.dependency_overrides[get_readiness_probes] = lambda: probes  # type: ignore[attr-defined]
    r = client.get("/api/v1/health/ready")
    assert r.status_code == expected_http
    assert r.json()["status"] == expected_overall
    assert [c["status"] for c in r.json()["components"]] == states


def test_overall_status_empty_is_ok() -> None:
    assert overall_status([]) == "ok"


def test_not_found_uses_error_envelope(client: TestClient) -> None:
    r = client.get("/api/v1/does-not-exist", headers={"X-Request-ID": "rid-1"})
    assert r.status_code == 404
    assert r.json() == {
        "error": {
            "code": "not_found",
            "message": "Not Found",
            "details": None,
            "request_id": "rid-1",
        }
    }


def test_validation_error_envelope(client: TestClient) -> None:
    # Body validation happens before the DB dependency is used.
    r = client.post("/api/v1/auth/login", json={"email": "a@b.co"})
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "validation_error"
    assert any("password" in d["loc"] for d in err["details"])


@pytest.mark.parametrize(
    "path", ["/api/v1/data-sources", "/api/v1/fleet/positions", "/api/v1/shipments",
             "/api/v1/research/experiments", "/api/v1/org/members", "/api/v1/auth/me"],
)  # fmt: skip
def test_data_endpoints_require_authentication(client: TestClient, path: str) -> None:
    r = client.get(path)
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "not_authenticated"
    assert r.headers["WWW-Authenticate"].startswith("Bearer")


def test_malformed_tokens_are_rejected_without_a_database(client: TestClient) -> None:
    r = client.get("/api/v1/shipments", headers={"Authorization": "Bearer not-a-jwt"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_token"


def test_unhandled_error_does_not_leak_internals(client: TestClient) -> None:
    r = client.get("/__boom")
    assert r.status_code == 500
    assert r.json()["error"]["code"] == "internal_error"
    assert "secret internal detail" not in r.text


def test_openapi_is_served(client: TestClient) -> None:
    r = client.get("/api/v1/openapi.json")
    assert r.status_code == 200
    paths = r.json()["paths"]
    assert {
        "/api/v1/health",
        "/api/v1/health/ready",
        "/api/v1/data-sources",
        "/api/v1/carriers",
    } <= set(paths)


NOW = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("last", "interval", "expected"),
    [
        (None, 60, "never"),
        (NOW - timedelta(seconds=30), None, "unknown"),
        (NOW - timedelta(seconds=119), 60, "fresh"),
        (NOW - timedelta(seconds=121), 60, "stale"),
    ],
)
def test_compute_freshness(last: datetime | None, interval: int | None, expected: str) -> None:
    assert compute_freshness(last, interval, NOW) == expected


def test_metrics_use_route_templates_and_can_require_a_token() -> None:
    from core.settings import Settings

    with TestClient(create_app(), raise_server_exceptions=False) as c:
        c.get("/api/v1/health")
        c.get("/api/v1/shipments/00000000-0000-0000-0000-000000000001")  # 401, but templated
        body = c.get("/metrics").text
    assert 'route="/api/v1/health"' in body
    assert 'route="/api/v1/shipments/{shipment_id}"' in body
    assert "00000000-0000-0000-0000-000000000001" not in body  # bounded cardinality
    assert "chainsight_auth_events_total" in body

    secured = Settings(_env_file=None, metrics_token="m" * 20)  # type: ignore[call-arg]
    with TestClient(create_app(secured)) as c:
        assert c.get("/metrics").status_code == 401
        assert c.get("/metrics", headers={"Authorization": "Bearer " + "m" * 20}).status_code == 200


def test_metrics_are_not_reachable_through_the_versioned_api(client: TestClient) -> None:
    assert client.get("/api/v1/metrics").status_code == 404
