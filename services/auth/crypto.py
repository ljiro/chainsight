"""Password hashing, JWT access tokens, opaque refresh tokens and API keys."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from core.logging_config import get_logger
from core.settings import Settings

log = get_logger(__name__)

JWT_ALGORITHM = "HS256"
JWT_ISSUER = "chainsight"
JWT_AUDIENCE = "chainsight-api"
API_KEY_PREFIX = "cs_"
MIN_PASSWORD_LENGTH = 12

# argon2-cffi defaults to Argon2id with RFC 9106's low-memory profile (64 MiB, t=3, p=4).
_hasher = PasswordHasher()
# Verified against when the email is unknown, so response time does not reveal accounts.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


class InvalidToken(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code  # token_expired | invalid_token


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def password_problems(password: str, email: str | None = None) -> list[str]:
    problems = []
    if len(password) < MIN_PASSWORD_LENGTH:
        problems.append(f"must be at least {MIN_PASSWORD_LENGTH} characters")
    if len(password) > 256:
        problems.append("must be at most 256 characters")
    if email and password.lower() == email.lower():
        problems.append("must not be your email address")
    if len(set(password)) < 5:
        problems.append("must contain at least 5 different characters")
    return problems


@lru_cache
def _ephemeral_secret() -> str:
    log.warning(
        "jwt_secret_ephemeral",
        hint="JWT_SECRET is not set; sessions end when the API restarts. Set it in .env.",
    )
    return secrets.token_urlsafe(48)


def jwt_secret(settings: Settings) -> str:
    if settings.jwt_secret is not None:
        return settings.jwt_secret.get_secret_value()
    return _ephemeral_secret()  # never reached in production (settings refuse to load)


@dataclass(frozen=True)
class AccessClaims:
    user_id: uuid.UUID
    organization_id: uuid.UUID
    session_id: uuid.UUID
    role: str
    expires_at: datetime


def issue_access_token(
    settings: Settings,
    *,
    user_id: uuid.UUID,
    organization_id: uuid.UUID,
    session_id: uuid.UUID,
    role: str,
    now: datetime,
) -> tuple[str, datetime]:
    exp = now + timedelta(minutes=settings.jwt_access_ttl_minutes)
    payload: dict[str, Any] = {
        "iss": JWT_ISSUER, "aud": JWT_AUDIENCE, "typ": "access",
        "sub": str(user_id), "org": str(organization_id), "sid": str(session_id), "role": role,
        "iat": int(now.timestamp()), "exp": int(exp.timestamp()), "jti": secrets.token_hex(8),
    }  # fmt: skip
    return jwt.encode(payload, jwt_secret(settings), algorithm=JWT_ALGORITHM), exp


def decode_access_token(settings: Settings, token: str) -> AccessClaims:
    try:
        p = jwt.decode(
            token, jwt_secret(settings), algorithms=[JWT_ALGORITHM],
            audience=JWT_AUDIENCE, issuer=JWT_ISSUER,
            options={"require": ["exp", "iat", "sub", "org", "sid", "role"]},
        )  # fmt: skip
    except jwt.ExpiredSignatureError as exc:
        raise InvalidToken("token_expired") from exc
    except jwt.PyJWTError as exc:
        raise InvalidToken("invalid_token") from exc
    if p.get("typ") != "access":
        raise InvalidToken("invalid_token")
    try:
        return AccessClaims(
            user_id=uuid.UUID(p["sub"]), organization_id=uuid.UUID(p["org"]),
            session_id=uuid.UUID(p["sid"]), role=str(p["role"]),
            expires_at=datetime.fromtimestamp(p["exp"], tz=UTC),
        )  # fmt: skip
    except (ValueError, TypeError) as exc:
        raise InvalidToken("invalid_token") from exc


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def new_refresh_token() -> tuple[str, str]:
    """(token, sha256 hash). Only the hash is stored."""
    token = secrets.token_urlsafe(32)
    return token, sha256_hex(token)


def new_api_key() -> tuple[str, str, str]:
    """(plaintext `cs_<prefix>_<secret>`, prefix, sha256 hash). The plaintext is shown once."""
    prefix = secrets.token_hex(4)
    key = f"{API_KEY_PREFIX}{prefix}_{secrets.token_urlsafe(32)}"
    return key, prefix, sha256_hex(key)


def parse_api_key(key: str) -> str | None:
    """Return the lookup prefix of a well-formed key, else None."""
    if not key.startswith(API_KEY_PREFIX) or len(key) > 200:
        return None
    prefix, sep, secret = key[len(API_KEY_PREFIX) :].partition("_")
    if not sep or len(prefix) != 8 or len(secret) < 20:
        return None
    return prefix


def api_key_matches(key: str, stored_hash: str) -> bool:
    return hmac.compare_digest(sha256_hex(key), stored_hash)
