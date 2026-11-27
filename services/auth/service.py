"""Authentication state in the database: users, memberships, sessions and API keys.

Every function that reads or writes org-owned data takes an `organization_id`; callers get
it from the authenticated principal, never from the request body.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from core.logging_config import get_logger
from core.settings import Settings
from database.enums import MemberRole
from services.auth import crypto
from services.auth.rbac import Permission, parse_scopes, permissions_for

log = get_logger(__name__)

LOCAL_ORG_SLUG = "local"


class AuthError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


@dataclass(frozen=True)
class Principal:
    """Who is calling, in which organization, with which permissions."""

    organization_id: uuid.UUID
    role: MemberRole | None
    permissions: frozenset[Permission]
    method: str  # session | api_key
    user_id: uuid.UUID | None = None
    api_key_id: uuid.UUID | None = None
    session_id: uuid.UUID | None = None
    email: str | None = None
    extra: dict[str, str] = field(default_factory=dict)

    def can(self, permission: Permission) -> bool:
        return permission in self.permissions

    @property
    def subject(self) -> str:
        return f"user:{self.user_id}" if self.user_id else f"api_key:{self.api_key_id}"


@dataclass(frozen=True)
class IssuedSession:
    access_token: str
    access_expires_at: datetime
    refresh_token: str
    refresh_expires_at: datetime
    session_id: uuid.UUID
    user_id: uuid.UUID
    organization_id: uuid.UUID
    role: MemberRole


async def local_org_id(session: AsyncSession) -> uuid.UUID:
    org: uuid.UUID = (
        await session.execute(
            sa.text("SELECT id FROM organizations WHERE slug = :s"), {"s": LOCAL_ORG_SLUG}
        )
    ).scalar_one()
    return org


async def _membership(
    session: AsyncSession, user_id: uuid.UUID, org_slug: str | None
) -> tuple[uuid.UUID, MemberRole] | None:
    row = (
        await session.execute(
            sa.text(
                "SELECT m.organization_id, m.role FROM organization_members m "
                "JOIN organizations o ON o.id = m.organization_id WHERE m.user_id = :u "
                "AND (CAST(:slug AS text) IS NULL OR o.slug = :slug) ORDER BY m.created_at LIMIT 1"
            ),
            {"u": user_id, "slug": org_slug},
        )
    ).one_or_none()
    return (row.organization_id, MemberRole(row.role)) if row else None


async def _issue(
    session: AsyncSession,
    settings: Settings,
    *,
    user_id: uuid.UUID,
    org_id: uuid.UUID,
    role: MemberRole,
    now: datetime,
    ip: str | None,
    user_agent: str | None,
    session_id: uuid.UUID | None = None,
) -> IssuedSession:
    refresh, refresh_hash = crypto.new_refresh_token()
    refresh_exp = now + timedelta(days=settings.auth_refresh_ttl_days)
    if session_id is None:
        session_id = (
            await session.execute(
                sa.text(
                    "INSERT INTO auth_sessions (user_id, organization_id, refresh_hash, expires_at, "
                    "ip_address, user_agent, last_used_at) VALUES (:u, :o, :h, :e, "
                    "CAST(:ip AS inet), :ua, :now) RETURNING id"
                ),
                {"u": user_id, "o": org_id, "h": refresh_hash, "e": refresh_exp, "ip": ip,
                 "ua": (user_agent or "")[:500] or None, "now": now},
            )
        ).scalar_one()  # fmt: skip
    else:
        await session.execute(
            sa.text(
                "UPDATE auth_sessions SET refresh_hash = :h, expires_at = :e, last_used_at = :now "
                "WHERE id = :s"
            ),
            {"h": refresh_hash, "e": refresh_exp, "now": now, "s": session_id},
        )
    assert session_id is not None
    access, access_exp = crypto.issue_access_token(
        settings, user_id=user_id, organization_id=org_id, session_id=session_id,
        role=role.value, now=now,
    )  # fmt: skip
    return IssuedSession(
        access, access_exp, refresh, refresh_exp, session_id, user_id, org_id, role
    )


async def login(
    session: AsyncSession,
    settings: Settings,
    *,
    email: str,
    password: str,
    now: datetime,
    org_slug: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> IssuedSession:
    """Check credentials and open a session. One generic error for every failure reason."""
    user = (
        await session.execute(
            sa.text(
                "SELECT id, password_hash, is_active FROM users WHERE lower(email) = lower(:e)"
            ),
            {"e": email.strip()},
        )
    ).one_or_none()
    ok = crypto.verify_password(user.password_hash if user else None, password)
    if not (user and ok and user.is_active):
        raise AuthError("invalid_credentials", "Email or password is incorrect")
    membership = await _membership(session, user.id, org_slug)
    if membership is None:
        raise AuthError("invalid_credentials", "Email or password is incorrect")
    if crypto.needs_rehash(user.password_hash):
        await session.execute(
            sa.text("UPDATE users SET password_hash = :h WHERE id = :u"),
            {"h": crypto.hash_password(password), "u": user.id},
        )
    await session.execute(
        sa.text("UPDATE users SET last_login_at = :now WHERE id = :u"), {"now": now, "u": user.id}
    )
    org_id, role = membership
    return await _issue(
        session, settings, user_id=user.id, org_id=org_id, role=role, now=now, ip=ip,
        user_agent=user_agent,
    )  # fmt: skip


async def refresh(
    session: AsyncSession, settings: Settings, *, refresh_token: str, now: datetime
) -> IssuedSession:
    """Rotate a refresh token. Reusing an already-rotated token revokes nothing new but fails;
    the role is re-read so demotions take effect at the next refresh."""
    row = (
        await session.execute(
            sa.text(
                "SELECT s.id, s.user_id, s.organization_id, s.expires_at, s.revoked_at, u.is_active "
                "FROM auth_sessions s JOIN users u ON u.id = s.user_id WHERE s.refresh_hash = :h "
                "FOR UPDATE OF s"
            ),
            {"h": crypto.sha256_hex(refresh_token)},
        )
    ).one_or_none()
    if row is None or row.revoked_at is not None or row.expires_at <= now or not row.is_active:
        raise AuthError("session_expired", "Your session has ended; sign in again")
    role = (
        await session.execute(
            sa.text(
                "SELECT role FROM organization_members WHERE user_id = :u AND organization_id = :o"
            ),
            {"u": row.user_id, "o": row.organization_id},
        )
    ).scalar_one_or_none()
    if role is None:
        await revoke_session(session, row.id, now)
        raise AuthError("session_expired", "Your session has ended; sign in again")
    return await _issue(
        session, settings, user_id=row.user_id, org_id=row.organization_id,
        role=MemberRole(role), now=now, ip=None, user_agent=None, session_id=row.id,
    )  # fmt: skip


async def revoke_session(session: AsyncSession, session_id: uuid.UUID, now: datetime) -> None:
    await session.execute(
        sa.text("UPDATE auth_sessions SET revoked_at = :now WHERE id = :s AND revoked_at IS NULL"),
        {"now": now, "s": session_id},
    )


async def revoke_user_sessions(
    session: AsyncSession, user_id: uuid.UUID, now: datetime, *, except_session: uuid.UUID | None
) -> None:
    await session.execute(
        sa.text(
            "UPDATE auth_sessions SET revoked_at = :now WHERE user_id = :u AND revoked_at IS NULL "
            "AND (CAST(:keep AS uuid) IS NULL OR id <> :keep)"
        ),
        {"now": now, "u": user_id, "keep": except_session},
    )


async def session_principal(session: AsyncSession, claims: crypto.AccessClaims) -> Principal:
    """Resolve a verified access token. The session must still be live and the membership
    present, so logout, deactivation and removal take effect immediately."""
    row = (
        await session.execute(
            sa.text(
                "SELECT u.email, m.role FROM auth_sessions s JOIN users u ON u.id = s.user_id "
                "JOIN organization_members m ON m.user_id = s.user_id "
                "AND m.organization_id = s.organization_id "
                "WHERE s.id = :s AND s.user_id = :u AND s.organization_id = :o "
                "AND s.revoked_at IS NULL AND u.is_active"
            ),
            {"s": claims.session_id, "u": claims.user_id, "o": claims.organization_id},
        )
    ).one_or_none()
    if row is None:
        raise AuthError("session_expired", "Your session has ended; sign in again")
    role = MemberRole(row.role)
    return Principal(
        organization_id=claims.organization_id, role=role, permissions=permissions_for(role),
        method="session", user_id=claims.user_id, session_id=claims.session_id, email=row.email,
    )  # fmt: skip


async def api_key_principal(session: AsyncSession, key: str, now: datetime) -> Principal:
    prefix = crypto.parse_api_key(key)
    row = None
    if prefix is not None:
        row = (
            await session.execute(
                sa.text(
                    "SELECT id, organization_id, key_hash, scopes, expires_at, revoked_at "
                    "FROM api_keys WHERE key_prefix = :p"
                ),
                {"p": prefix},
            )
        ).one_or_none()
    if (
        row is None
        or not crypto.api_key_matches(key, row.key_hash)
        or row.revoked_at is not None
        or (row.expires_at is not None and row.expires_at <= now)
    ):
        raise AuthError("invalid_api_key", "API key is invalid, expired or revoked")
    # Throttle last_used_at writes to one per minute per key.
    await session.execute(
        sa.text(
            "UPDATE api_keys SET last_used_at = :now WHERE id = :k "
            "AND (last_used_at IS NULL OR last_used_at < :now - interval '1 minute')"
        ),
        {"now": now, "k": row.id},
    )
    return Principal(
        organization_id=row.organization_id, role=None, permissions=parse_scopes(row.scopes),
        method="api_key", api_key_id=row.id,
    )  # fmt: skip


async def create_user(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    organization_id: uuid.UUID,
    role: MemberRole,
    full_name: str | None = None,
) -> uuid.UUID:
    problems = crypto.password_problems(password, email)
    if problems:
        raise AuthError("weak_password", "Password " + "; ".join(problems))
    existing = (
        await session.execute(
            sa.text("SELECT id FROM users WHERE lower(email) = lower(:e)"), {"e": email.strip()}
        )
    ).scalar_one_or_none()
    if existing is None:
        user_id: uuid.UUID = (
            await session.execute(
                sa.text(
                    "INSERT INTO users (email, full_name, password_hash) VALUES (:e, :n, :h) "
                    "RETURNING id"
                ),
                {"e": email.strip(), "n": full_name, "h": crypto.hash_password(password)},
            )
        ).scalar_one()
    else:
        raise AuthError("email_taken", "A user with this email already exists")
    await session.execute(
        sa.text(
            "INSERT INTO organization_members (organization_id, user_id, role) VALUES (:o, :u, :r)"
        ),
        {"o": organization_id, "u": user_id, "r": role.value},
    )
    return user_id


async def ensure_bootstrap_owner(session: AsyncSession, settings: Settings) -> bool:
    """Create the first owner from BOOTSTRAP_ADMIN_* if there are no users at all."""
    if not (settings.bootstrap_admin_email and settings.bootstrap_admin_password):
        return False
    if (await session.execute(sa.text("SELECT 1 FROM users LIMIT 1"))).scalar():
        return False
    await create_user(
        session,
        email=settings.bootstrap_admin_email,
        password=settings.bootstrap_admin_password.get_secret_value(),
        organization_id=await local_org_id(session),
        role=MemberRole.OWNER,
        full_name="Administrator",
    )
    log.info("bootstrap_owner_created", email=settings.bootstrap_admin_email)
    return True
