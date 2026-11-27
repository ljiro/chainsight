"""Authentication, RBAC, organization isolation, API keys, audit trail and rate limits."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

from chainsight_api.main import create_app
from core.settings import Settings, get_settings
from services.auth import crypto

PASSWORD = "integration passphrase 1"


def make_user(
    engine: Engine, role: str, *, org_slug: str = "local", email: str | None = None
) -> str:
    email = email or f"{role}-{uuid.uuid4().hex[:8]}@example.test"
    with engine.begin() as conn:
        org = conn.execute(
            sa.text("SELECT id FROM organizations WHERE slug = :s"), {"s": org_slug}
        ).scalar()
        if org is None:
            org = conn.execute(sa.text("INSERT INTO organizations (name, slug) VALUES (:s, :s) RETURNING id"),
                               {"s": org_slug}).scalar_one()  # fmt: skip
        uid = conn.execute(
            sa.text("INSERT INTO users (email, password_hash) VALUES (:e, :h) RETURNING id"),
            {"e": email, "h": crypto.hash_password(PASSWORD)},
        ).scalar_one()
        conn.execute(sa.text("INSERT INTO organization_members (organization_id, user_id, role) VALUES (:o, :u, :r)"),
                     {"o": org, "u": uid, "r": role})  # fmt: skip
    return email


def login(c: TestClient, email: str, password: str = PASSWORD) -> Any:
    return c.post("/api/v1/auth/login", json={"email": email, "password": password})


CSRF = {"X-Requested-With": "chainsight"}


@pytest.fixture
def client(migrated_db: Engine) -> Iterator[TestClient]:
    with TestClient(create_app()) as c:
        yield c


def test_login_sets_httponly_cookies_and_me_works(client: TestClient, migrated_db: Engine) -> None:
    email = make_user(migrated_db, "analyst")
    r = login(client, email)
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "analyst" and "shipments:write" in r.json()["permissions"]
    assert "access_token" not in r.json()  # the token never reaches JavaScript
    cookies = r.headers.get_list("set-cookie")
    assert any(
        c.startswith("cs_access=") and "HttpOnly" in c and "SameSite=strict" in c for c in cookies
    )
    assert any(c.startswith("cs_refresh=") and "Path=/api/v1/auth" in c for c in cookies)
    me = client.get("/api/v1/auth/me").json()
    assert me["user"]["email"] == email and me["organization"]["slug"] == "local"
    assert client.get("/api/v1/shipments").status_code == 200


def test_bad_credentials_get_one_generic_error_and_are_audited(
    client: TestClient, migrated_db: Engine
) -> None:
    email = make_user(migrated_db, "viewer")
    wrong = login(client, email, "not the password at all")
    unknown = login(client, "nobody@example.test")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["error"]["message"] == unknown.json()["error"]["message"]
    with migrated_db.connect() as conn:
        n = conn.execute(sa.text("SELECT count(*) FROM audit_logs WHERE action = 'auth.login_failed' "
                                 "AND changes->>'email_hash' = :h"), {"h": crypto.sha256_hex(email)}).scalar()  # fmt: skip
    assert n == 1


def test_cookie_writes_need_the_csrf_header(client: TestClient, migrated_db: Engine) -> None:
    login(client, make_user(migrated_db, "analyst"))
    body = {"carrier": "demo", "reference": "DEMO1234567"}
    blocked = client.post("/api/v1/shipments/track", json=body)
    assert blocked.status_code == 403 and blocked.json()["error"]["code"] == "csrf_check_failed"
    assert client.post("/api/v1/shipments/track", json=body, headers=CSRF).status_code == 200


def test_refresh_rotates_and_logout_revokes_immediately(
    client: TestClient, migrated_db: Engine
) -> None:
    login(client, make_user(migrated_db, "viewer"))
    old_refresh = client.cookies.get("cs_refresh")
    r = client.post("/api/v1/auth/refresh")
    assert r.status_code == 200 and client.cookies.get("cs_refresh") != old_refresh
    with TestClient(create_app(), cookies={"cs_refresh": old_refresh or ""}) as replay:
        assert replay.post("/api/v1/auth/refresh").status_code == 401  # rotated token is dead
    access = client.cookies.get("cs_access")
    assert client.post("/api/v1/auth/logout").status_code == 204
    with TestClient(create_app(), cookies={"cs_access": access or ""}) as stale:
        r = stale.get("/api/v1/auth/me")
    assert r.status_code == 401 and r.json()["error"]["code"] == "session_expired"


def test_roles_gate_writes_and_administration(client: TestClient, migrated_db: Engine) -> None:
    login(client, make_user(migrated_db, "viewer"))
    assert client.get("/api/v1/shipments").status_code == 200
    r = client.post(
        "/api/v1/shipments/track", json={"carrier": "demo", "reference": "DEMO1"}, headers=CSRF
    )
    assert (
        r.status_code == 403
        and r.json()["error"]["details"]["required_permission"] == "shipments:write"
    )
    assert client.get("/api/v1/org/api-keys").status_code == 403
    assert client.get("/api/v1/org/audit-logs").status_code == 403

    with TestClient(create_app()) as admin:
        login(admin, make_user(migrated_db, "admin"))
        made = admin.post("/api/v1/org/members", headers=CSRF, json={
            "email": f"new-{uuid.uuid4().hex[:6]}@example.test", "role": "analyst", "password": PASSWORD})  # fmt: skip
        assert made.status_code == 201, made.text
        owner_try = admin.post("/api/v1/org/members", headers=CSRF, json={
            "email": f"own-{uuid.uuid4().hex[:6]}@example.test", "role": "owner", "password": PASSWORD})  # fmt: skip
        assert owner_try.status_code == 403
        weak = admin.post("/api/v1/org/members", headers=CSRF, json={
            "email": f"weak-{uuid.uuid4().hex[:6]}@example.test", "role": "viewer", "password": "short"})  # fmt: skip
        assert weak.status_code == 422 and weak.json()["error"]["code"] == "weak_password"
        uid = made.json()["user_id"]
        assert (
            admin.patch(f"/api/v1/org/members/{uid}", json={"role": "viewer"}, headers=CSRF).json()[
                "role"
            ]
            == "viewer"
        )
        audit = admin.get("/api/v1/org/audit-logs", params={"action": "member."}).json()
        assert {"member.added", "member.updated"} <= {e["action"] for e in audit["items"]}


def test_the_last_owner_cannot_be_removed_or_demoted(migrated_db: Engine) -> None:
    slug = f"solo-{uuid.uuid4().hex[:6]}"
    owner = make_user(migrated_db, "owner", org_slug=slug)
    second = make_user(migrated_db, "admin", org_slug=slug)
    with TestClient(create_app()) as c:
        login(c, second)
        members = c.get("/api/v1/org/members").json()
        owner_id = next(m["user_id"] for m in members if m["email"] == owner)
        assert (
            c.patch(
                f"/api/v1/org/members/{owner_id}", json={"role": "viewer"}, headers=CSRF
            ).status_code
            == 403
        )
        assert c.delete(f"/api/v1/org/members/{owner_id}", headers=CSRF).status_code == 403
    with TestClient(create_app()) as c:
        login(c, owner)
        me_id = c.get("/api/v1/auth/me").json()["user"]["id"]
        assert (
            c.patch(
                f"/api/v1/org/members/{me_id}", json={"role": "viewer"}, headers=CSRF
            ).status_code
            == 409
        )


def test_organizations_cannot_see_each_others_data(migrated_db: Engine) -> None:
    with TestClient(create_app()) as a:
        login(a, make_user(migrated_db, "analyst"))
        tracked = a.post(
            "/api/v1/shipments/track",
            json={"carrier": "demo", "reference": "DEMO7654321"},
            headers=CSRF,
        )
        assert tracked.status_code == 200
        sid = tracked.json()["id"]
    with TestClient(create_app()) as b:
        login(b, make_user(migrated_db, "owner", org_slug=f"other-{uuid.uuid4().hex[:6]}"))
        assert b.get(f"/api/v1/shipments/{sid}").status_code == 404
        assert b.post(f"/api/v1/shipments/{sid}/refresh", headers=CSRF).status_code == 404
        assert b.get("/api/v1/shipments").json()["total"] == 0
        assert b.get(f"/api/v1/shipments/{sid}/delay-risk").json() == []
        assert b.get("/api/v1/research/experiments").json() == []
        assert all(m["email"].endswith("example.test") for m in b.get("/api/v1/org/members").json())
        assert len(b.get("/api/v1/org/members").json()) == 1


def test_api_keys_are_shown_once_scoped_and_revocable(migrated_db: Engine) -> None:
    with TestClient(create_app()) as admin:
        login(admin, make_user(migrated_db, "admin"))
        created = admin.post(
            "/api/v1/org/api-keys", json={"name": "ro", "role": "viewer"}, headers=CSRF
        )
        assert created.status_code == 201
        key = created.json()["key"]
        listed = admin.get("/api/v1/org/api-keys").json()
        assert all("key" not in k and "key_hash" not in k for k in listed)
        assert (
            admin.post(
                "/api/v1/org/api-keys", json={"name": "x", "role": "owner"}, headers=CSRF
            ).status_code
            == 422
        )
    with TestClient(create_app(), headers={"X-API-Key": key}) as k:
        assert k.get("/api/v1/shipments").status_code == 200
        assert k.get("/api/v1/auth/me").json()["auth_method"] == "api_key"
        # API keys are not cookies: no CSRF header needed, but the scope still applies.
        assert (
            k.post(
                "/api/v1/shipments/track", json={"carrier": "demo", "reference": "DEMO1"}
            ).status_code
            == 403
        )
    with TestClient(create_app(), headers={"Authorization": f"Bearer {key}"}) as k:
        assert k.get("/api/v1/shipments").status_code == 200
    admin_c = TestClient(create_app())
    with admin_c:
        login(admin_c, make_user(migrated_db, "admin"))
        assert (
            admin_c.delete(f"/api/v1/org/api-keys/{created.json()['id']}", headers=CSRF).status_code
            == 204
        )
    with TestClient(create_app(), headers={"X-API-Key": key}) as k:
        assert k.get("/api/v1/shipments").json()["error"]["code"] == "invalid_api_key"


def test_deactivated_users_lose_access_at_once(migrated_db: Engine) -> None:
    target = make_user(migrated_db, "analyst")
    with TestClient(create_app()) as victim, TestClient(create_app()) as admin:
        login(victim, target)
        login(admin, make_user(migrated_db, "admin"))
        uid = next(
            m["user_id"] for m in admin.get("/api/v1/org/members").json() if m["email"] == target
        )
        assert (
            admin.patch(
                f"/api/v1/org/members/{uid}", json={"is_active": False}, headers=CSRF
            ).status_code
            == 200
        )
        assert victim.get("/api/v1/shipments").status_code == 401
        assert login(victim, target).status_code == 401


def test_audit_log_is_append_only(migrated_db: Engine) -> None:
    with migrated_db.begin() as conn:
        aid = conn.execute(
            sa.text("INSERT INTO audit_logs (action) VALUES ('test.event') RETURNING id")
        ).scalar_one()
    for stmt in (
        "UPDATE audit_logs SET action = 'tampered' WHERE id = :i",
        "DELETE FROM audit_logs WHERE id = :i",
    ):
        with pytest.raises(sa.exc.DBAPIError, match="append-only"), migrated_db.begin() as conn:
            conn.execute(sa.text(stmt), {"i": aid})
    # Deleting the actor still works (ON DELETE SET NULL is the one allowed change).
    email = make_user(migrated_db, "viewer")
    with migrated_db.begin() as conn:
        uid = conn.execute(
            sa.text("SELECT id FROM users WHERE email = :e"), {"e": email}
        ).scalar_one()
        conn.execute(
            sa.text("INSERT INTO audit_logs (action, actor_user_id) VALUES ('test.actor', :u)"),
            {"u": uid},
        )
        conn.execute(sa.text("DELETE FROM users WHERE id = :u"), {"u": uid})


def test_login_is_rate_limited(migrated_db: Engine) -> None:
    settings = Settings(_env_file=None, rate_limit_enabled=True, rate_limit_login_per_minute=3)  # type: ignore[call-arg]
    email = f"rl-{uuid.uuid4().hex}@example.test"
    with TestClient(create_app(settings)) as c:
        codes = [login(c, email, "wrong password here").status_code for _ in range(5)]
        assert codes[:3] == [401, 401, 401] and codes[3] == 429
        limited = login(c, email, "wrong password here")
        assert int(limited.headers["Retry-After"]) > 0
        assert limited.json()["error"]["code"] == "rate_limited"


def test_bootstrap_owner_is_created_once(migrated_db: Engine) -> None:
    import asyncio

    from database.session import get_sessionmaker
    from services.auth.service import ensure_bootstrap_owner

    settings = Settings(_env_file=None, bootstrap_admin_email="boot@example.test",  # type: ignore[call-arg]
                        bootstrap_admin_password=PASSWORD)  # fmt: skip

    async def run() -> bool:
        async with get_sessionmaker()() as s, s.begin():
            return await ensure_bootstrap_owner(s, settings)

    assert asyncio.run(run()) is False  # users already exist: never overrides
    get_settings.cache_clear()


def test_websocket_requires_authentication(client: TestClient) -> None:
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect), client.websocket_connect("/api/v1/ws/positions") as ws:
        ws.receive_json()


def _push(c: TestClient, events: list[dict[str, Any]], *, delivery: str, secret: str = "whsec-test",
          ts: int | None = None) -> Any:  # fmt: skip
    import json
    import time

    from services.shipment_tracking import webhooks as wh

    body = json.dumps(events).encode()
    t = int(time.time()) if ts is None else ts
    return c.post("/api/v1/webhooks/carriers/demo", content=body, headers={
        "Content-Type": "application/json", wh.DELIVERY_HEADER: delivery,
        wh.SIGNATURE_HEADER: f"t={t},v1={wh.sign(secret, t, body)}"})  # fmt: skip


def _event(ref: str, eid: str) -> dict[str, Any]:
    return {"eventID": eid, "eventType": "TRANSPORT", "eventClassifierCode": "ACT",
            "transportEventTypeCode": "DEPA", "eventDateTime": "2026-11-20T08:00:00Z",
            "eventCreatedDateTime": "2026-11-20T08:05:00Z",
            "documentReferences": [{"documentReferenceType": "BKG", "documentReferenceValue": ref}],
            "transportCall": {"UNLocationCode": "NLRTM", "modeOfTransport": "VESSEL"}}  # fmt: skip


def test_signed_carrier_push_updates_only_tracked_shipments(migrated_db: Engine) -> None:
    import time

    settings = Settings(_env_file=None, carrier_webhook_secrets='{"demo": "whsec-test"}')  # type: ignore[call-arg]
    ref = f"DEMO{uuid.uuid4().int % 10**7:07d}"
    with TestClient(create_app()) as a:
        login(a, make_user(migrated_db, "analyst"))
        assert (
            a.post(
                "/api/v1/shipments/track", json={"carrier": "demo", "reference": ref}, headers=CSRF
            ).status_code
            == 200
        )
    with TestClient(create_app(settings)) as c:
        delivery = uuid.uuid4().hex
        ok = _push(
            c, [_event(ref, f"push-{delivery}"), _event("NOTTRACKED1", "x1")], delivery=delivery
        )
        assert ok.status_code == 202, ok.text
        expected = {
            "status": "processed",
            "events_inserted": 1,
            "unmatched_events": 1,
            "shipments_updated": 1,
        }
        assert expected.items() <= ok.json().items()
        replay = _push(c, [_event(ref, f"push-{delivery}")], delivery=delivery)
        assert replay.status_code == 200 and replay.json()["status"] == "duplicate"
        assert _push(c, [_event(ref, "y")], delivery="d2", secret="wrong").status_code == 401
        assert (
            _push(c, [_event(ref, "y")], delivery="d3", ts=int(time.time()) - 3600).json()["error"][
                "code"
            ]
            == "signature_expired"
        )
        assert (
            c.post("/api/v1/webhooks/carriers/msc", content=b"[]").status_code == 404
        )  # not configured
    with migrated_db.connect() as conn:
        n = conn.execute(sa.text("SELECT count(*) FROM shipment_events e JOIN shipments s ON s.id = e.shipment_id "
                                 "WHERE s.reference_value = :r AND e.source_event_id = :e"),
                         {"r": ref, "e": f"push-{delivery}"}).scalar()  # fmt: skip
        created = conn.execute(
            sa.text("SELECT count(*) FROM shipments WHERE reference_value = 'NOTTRACKED1'")
        ).scalar()
    assert n == 1 and created == 0
