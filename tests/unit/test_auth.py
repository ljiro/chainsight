from __future__ import annotations

import uuid
from datetime import timedelta

import jwt
import pytest

from core.settings import Settings
from core.timeutils import utcnow
from database.enums import MemberRole
from services.auth import crypto
from services.auth.rbac import Permission, can_assign, parse_scopes, permissions_for


def settings(**kw: object) -> Settings:
    return Settings(_env_file=None, **({"jwt_secret": "s" * 40} | kw))  # type: ignore[arg-type]


def test_passwords_use_argon2id_and_verify() -> None:
    h = crypto.hash_password("correct horse battery")
    assert h.startswith("$argon2id$")
    assert crypto.verify_password(h, "correct horse battery")
    assert not crypto.verify_password(h, "wrong horse battery")
    assert not crypto.verify_password(None, "anything")  # unknown user: still runs a verify
    assert not crypto.verify_password("not-a-hash", "x")


def test_password_policy() -> None:
    assert crypto.password_problems("short") != []
    assert crypto.password_problems("aaaaaaaaaaaaaaa") != []  # too few distinct characters
    assert crypto.password_problems("me@example.com", "ME@example.com") != []
    assert crypto.password_problems("a long passphrase ok") == []


def test_access_token_round_trip_and_claims() -> None:
    s, now = settings(), utcnow()
    ids = {k: uuid.uuid4() for k in ("u", "o", "s")}
    token, exp = crypto.issue_access_token(
        s, user_id=ids["u"], organization_id=ids["o"], session_id=ids["s"], role="analyst", now=now
    )
    claims = crypto.decode_access_token(s, token)
    assert (claims.user_id, claims.organization_id, claims.session_id) == (
        ids["u"],
        ids["o"],
        ids["s"],
    )
    assert claims.role == "analyst" and exp - now == timedelta(minutes=15)


def test_expired_forged_and_wrong_type_tokens_are_rejected() -> None:
    s = settings()
    ids = {"user_id": uuid.uuid4(), "organization_id": uuid.uuid4(), "session_id": uuid.uuid4()}
    old, _ = crypto.issue_access_token(s, **ids, role="viewer", now=utcnow() - timedelta(hours=1))
    with pytest.raises(crypto.InvalidToken) as e:
        crypto.decode_access_token(s, old)
    assert e.value.code == "token_expired"

    good, _ = crypto.issue_access_token(s, **ids, role="viewer", now=utcnow())
    with pytest.raises(crypto.InvalidToken):
        crypto.decode_access_token(settings(jwt_secret="t" * 40), good)  # other secret

    payload = jwt.decode(good, options={"verify_signature": False})
    for tampered in (
        jwt.encode(payload | {"typ": "refresh"}, "s" * 40, algorithm="HS256"),
        jwt.encode(payload, None, algorithm="none"),  # alg=none must never be accepted
        jwt.encode(payload | {"aud": "elsewhere"}, "s" * 40, algorithm="HS256"),
    ):
        with pytest.raises(crypto.InvalidToken):
            crypto.decode_access_token(s, tampered)


def test_api_keys_are_hashed_and_parsed_strictly() -> None:
    key, prefix, key_hash = crypto.new_api_key()
    assert key.startswith(f"cs_{prefix}_") and key not in key_hash and len(key_hash) == 64
    assert crypto.parse_api_key(key) == prefix
    assert crypto.api_key_matches(key, key_hash)
    assert not crypto.api_key_matches(key + "x", key_hash)
    for bad in (
        "cs_short_x",
        "xx_12345678_" + "a" * 30,
        "cs_12345678" + "a" * 30,
        "cs_" + "a" * 300,
    ):
        assert crypto.parse_api_key(bad) is None


def test_roles_are_nested_and_assignment_is_bounded() -> None:
    roles = [MemberRole.VIEWER, MemberRole.ANALYST, MemberRole.ADMIN, MemberRole.OWNER]
    v, a, ad, o = (permissions_for(r) for r in roles)
    assert v < a < ad < o
    assert Permission.SHIPMENTS_WRITE not in v and Permission.SHIPMENTS_WRITE in a
    assert Permission.API_KEYS_MANAGE not in a and Permission.ORG_MANAGE not in ad
    assert can_assign(MemberRole.ADMIN, MemberRole.ANALYST)
    assert can_assign(MemberRole.ADMIN, MemberRole.ADMIN)
    assert not can_assign(MemberRole.ADMIN, MemberRole.OWNER)
    assert not can_assign(MemberRole.ANALYST, MemberRole.VIEWER)
    assert can_assign(MemberRole.OWNER, MemberRole.OWNER)


def test_unknown_scopes_fail_closed() -> None:
    assert parse_scopes(["data:read", "root", "shipments:write"]) == {
        Permission.DATA_READ, Permission.SHIPMENTS_WRITE,
    }  # fmt: skip
    assert parse_scopes([]) == frozenset()
