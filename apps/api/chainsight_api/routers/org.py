"""Organization administration: members and roles, API keys, and the audit log.

Every query is scoped to the caller's organization; IDs from other organizations are 404s.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Annotated, Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from chainsight_api.auth import CurrentPrincipal, ReadPrincipal, client_ip, require
from chainsight_api.errors import ApiError
from chainsight_api.schemas.common import PageParams
from core.timeutils import utcnow
from database.enums import MemberRole
from database.session import session_scope
from services import audit
from services.auth import crypto
from services.auth import service as auth
from services.auth.rbac import ROLE_RANK, Permission, can_assign, permissions_for

router = APIRouter(prefix="/org", tags=["organization"])
Session = Annotated[AsyncSession, Depends(session_scope)]
Members = Annotated[auth.Principal, Depends(require(Permission.MEMBERS_MANAGE))]
Keys = Annotated[auth.Principal, Depends(require(Permission.API_KEYS_MANAGE))]
Auditor = Annotated[auth.Principal, Depends(require(Permission.AUDIT_READ))]
Owner = Annotated[auth.Principal, Depends(require(Permission.ORG_MANAGE))]


class OrgUpdate(BaseModel):
    name: str = Field(min_length=2, max_length=200)


class MemberCreate(BaseModel):
    email: str = Field(min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    full_name: str | None = Field(None, max_length=200)
    role: MemberRole
    password: str = Field(min_length=1, max_length=256, description="Initial password")


class MemberUpdate(BaseModel):
    role: MemberRole | None = None
    is_active: bool | None = None


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    role: MemberRole = Field(MemberRole.VIEWER, description="Permissions the key receives")
    expires_in_days: int | None = Field(90, ge=1, le=730, description="null = never expires")

    @field_validator("role")
    @classmethod
    def _no_owner_keys(cls, v: MemberRole) -> MemberRole:
        if v is MemberRole.OWNER:
            raise ValueError("API keys cannot hold the owner role")
        return v


@router.get("", summary="The caller's organization")
async def get_org(principal: ReadPrincipal, session: Session) -> dict[str, Any]:
    row = (
        await session.execute(
            sa.text(
                "SELECT o.id, o.name, o.slug, o.created_at, "
                "(SELECT count(*) FROM organization_members m WHERE m.organization_id = o.id) "
                "AS member_count FROM organizations o WHERE o.id = :o"
            ),
            {"o": principal.organization_id},
        )
    ).one()
    return dict(row._mapping)


@router.patch("", summary="Rename the organization (owner)")
async def update_org(
    request: Request, principal: Owner, session: Session, body: OrgUpdate
) -> dict[str, Any]:
    await session.execute(
        sa.text("UPDATE organizations SET name = :n, updated_at = now() WHERE id = :o"),
        {"n": body.name, "o": principal.organization_id},
    )
    await audit.record(session, "org.updated", principal=principal, entity_type="organization",
                       entity_id=principal.organization_id, changes={"name": body.name},
                       ip=client_ip(request))  # fmt: skip
    await session.commit()
    return await get_org(principal, session)


_MEMBERS = """
    SELECT u.id AS user_id, u.email, u.full_name, u.is_active, u.last_login_at, m.role,
           m.created_at AS joined_at
    FROM organization_members m JOIN users u ON u.id = m.user_id
    WHERE m.organization_id = :o
"""


@router.get("/members", summary="Members and their roles")
async def list_members(principal: ReadPrincipal, session: Session) -> list[dict[str, Any]]:
    rows = await session.execute(
        sa.text(_MEMBERS + " ORDER BY m.created_at"), {"o": principal.organization_id}
    )
    return [dict(r._mapping) for r in rows]


async def _member(session: AsyncSession, org: uuid.UUID, user_id: uuid.UUID) -> Any:
    row = (
        await session.execute(sa.text(_MEMBERS + " AND u.id = :u"), {"o": org, "u": user_id})
    ).one_or_none()
    if row is None:
        raise ApiError(404, "member_not_found", "Member not found")
    return row


async def _owner_count(session: AsyncSession, org: uuid.UUID) -> int:
    return int(
        (
            await session.execute(
                sa.text(
                    "SELECT count(*) FROM organization_members m JOIN users u ON u.id = m.user_id "
                    "WHERE m.organization_id = :o AND m.role = 'owner' AND u.is_active"
                ),
                {"o": org},
            )
        ).scalar_one()
    )


def _assert_can_assign(principal: auth.Principal, role: MemberRole) -> None:
    assert principal.role is not None or principal.method == "api_key"
    actor = principal.role or MemberRole.ADMIN  # API keys never hold owner
    if not can_assign(actor, role):
        raise ApiError(403, "forbidden", f"Your role cannot assign the {role.value} role")


@router.post("/members", status_code=201, summary="Add a member with an initial password")
async def add_member(
    request: Request, principal: Members, session: Session, body: MemberCreate
) -> dict[str, Any]:
    _assert_can_assign(principal, body.role)
    try:
        user_id = await auth.create_user(
            session, email=body.email, password=body.password, full_name=body.full_name,
            organization_id=principal.organization_id, role=body.role,
        )  # fmt: skip
    except auth.AuthError as exc:
        raise ApiError(409 if exc.code == "email_taken" else 422, exc.code, exc.message) from exc
    await audit.record(session, "member.added", principal=principal, entity_type="user",
                       entity_id=user_id, changes={"email": body.email, "role": body.role.value},
                       ip=client_ip(request))  # fmt: skip
    await session.commit()
    return dict((await _member(session, principal.organization_id, user_id))._mapping)


@router.patch("/members/{user_id}", summary="Change a member's role or deactivate them")
async def update_member(
    request: Request, principal: Members, session: Session, user_id: uuid.UUID, body: MemberUpdate
) -> dict[str, Any]:
    org = principal.organization_id
    current = await _member(session, org, user_id)
    current_role = MemberRole(current.role)
    if user_id == principal.user_id:
        raise ApiError(409, "cannot_modify_self", "Ask another admin to change your own access")
    # Touching an owner (or making someone owner) needs the owner role.
    _assert_can_assign(principal, current_role)
    changes: dict[str, Any] = {}
    if body.role is not None and body.role is not current_role:
        _assert_can_assign(principal, body.role)
        if current_role is MemberRole.OWNER and await _owner_count(session, org) <= 1:
            raise ApiError(409, "last_owner", "An organization needs at least one owner")
        await session.execute(
            sa.text("UPDATE organization_members SET role = :r WHERE organization_id = :o AND user_id = :u"),
            {"r": body.role.value, "o": org, "u": user_id},
        )  # fmt: skip
        changes["role"] = {"from": current_role.value, "to": body.role.value}
    if body.is_active is not None and body.is_active != current.is_active:
        if (
            not body.is_active
            and current_role is MemberRole.OWNER
            and await _owner_count(session, org) <= 1
        ):
            raise ApiError(409, "last_owner", "An organization needs at least one owner")  # fmt: skip
        await session.execute(
            sa.text("UPDATE users SET is_active = :a, updated_at = now() WHERE id = :u"),
            {"a": body.is_active, "u": user_id},
        )
        if not body.is_active:
            await auth.revoke_user_sessions(session, user_id, utcnow(), except_session=None)
        changes["is_active"] = body.is_active
    if changes:
        await audit.record(session, "member.updated", principal=principal, entity_type="user",
                           entity_id=user_id, changes=changes, ip=client_ip(request))  # fmt: skip
    await session.commit()
    return dict((await _member(session, org, user_id))._mapping)


@router.delete("/members/{user_id}", status_code=204, summary="Remove a member")
async def remove_member(
    request: Request, principal: Members, session: Session, user_id: uuid.UUID
) -> Response:
    org = principal.organization_id
    current = await _member(session, org, user_id)
    role = MemberRole(current.role)
    if user_id == principal.user_id:
        raise ApiError(409, "cannot_modify_self", "Ask another admin to remove you")
    _assert_can_assign(principal, role)
    if role is MemberRole.OWNER and await _owner_count(session, org) <= 1:
        raise ApiError(409, "last_owner", "An organization needs at least one owner")
    await session.execute(
        sa.text("DELETE FROM organization_members WHERE organization_id = :o AND user_id = :u"),
        {"o": org, "u": user_id},
    )
    await session.execute(
        sa.text("UPDATE auth_sessions SET revoked_at = now() WHERE user_id = :u "
                "AND organization_id = :o AND revoked_at IS NULL"), {"u": user_id, "o": org},
    )  # fmt: skip
    await audit.record(
        session, "member.removed", principal=principal, entity_type="user", entity_id=user_id,
        changes={"email": current.email}, ip=client_ip(request),
    )  # fmt: skip
    await session.commit()
    return Response(status_code=204)


_KEYS = """
    SELECT k.id, k.name, k.key_prefix, k.scopes, k.created_at, k.last_used_at, k.expires_at,
           k.revoked_at, u.email AS created_by
    FROM api_keys k LEFT JOIN users u ON u.id = k.created_by_user_id
    WHERE k.organization_id = :o
"""


@router.get("/api-keys", summary="API keys (never the key itself)")
async def list_api_keys(principal: Keys, session: Session) -> list[dict[str, Any]]:
    rows = await session.execute(
        sa.text(_KEYS + " ORDER BY k.created_at DESC"), {"o": principal.organization_id}
    )
    return [dict(r._mapping) for r in rows]


@router.post("/api-keys", status_code=201, summary="Create an API key (shown once)")
async def create_api_key(
    request: Request, principal: Keys, session: Session, body: ApiKeyCreate
) -> dict[str, Any]:
    granted = permissions_for(body.role)
    if not granted <= principal.permissions:
        raise ApiError(403, "forbidden", "A key cannot have more access than you")
    key, prefix, key_hash = crypto.new_api_key()
    now: datetime = utcnow()
    expires = None
    if body.expires_in_days:
        expires = now + timedelta(days=body.expires_in_days)
    key_id = (
        await session.execute(
            sa.text(
                "INSERT INTO api_keys (organization_id, created_by_user_id, name, key_prefix, "
                "key_hash, scopes, expires_at) VALUES (:o, :u, :n, :p, :h, :s, :e) RETURNING id"
            ),
            {"o": principal.organization_id, "u": principal.user_id, "n": body.name, "p": prefix,
             "h": key_hash, "s": sorted(p.value for p in granted), "e": expires},
        )
    ).scalar_one()  # fmt: skip
    await audit.record(session, "api_key.created", principal=principal, entity_type="api_key",
                       entity_id=key_id, changes={"name": body.name, "role": body.role.value,
                       "prefix": prefix}, ip=client_ip(request))  # fmt: skip
    await session.commit()
    row = (
        await session.execute(
            sa.text(_KEYS + " AND k.id = :k"), {"o": principal.organization_id, "k": key_id}
        )
    ).one()
    return dict(row._mapping) | {"key": key, "role": body.role.value}


@router.delete("/api-keys/{key_id}", status_code=204, summary="Revoke an API key")
async def revoke_api_key(
    request: Request, principal: Keys, session: Session, key_id: uuid.UUID
) -> Response:
    result = await session.execute(
        sa.text("UPDATE api_keys SET revoked_at = now() WHERE id = :k AND organization_id = :o "
                "AND revoked_at IS NULL RETURNING name"), {"k": key_id, "o": principal.organization_id},
    )  # fmt: skip
    name = result.scalar_one_or_none()
    if name is None:
        raise ApiError(404, "api_key_not_found", "API key not found or already revoked")
    await audit.record(session, "api_key.revoked", principal=principal, entity_type="api_key",
                       entity_id=key_id, changes={"name": name}, ip=client_ip(request))  # fmt: skip
    await session.commit()
    return Response(status_code=204)


@router.get("/audit-logs", summary="Audit trail (newest first)")
async def audit_logs(
    principal: Auditor,
    session: Session,
    page: Annotated[PageParams, Query()],
    action: Annotated[str | None, Query(max_length=100, description="Action prefix")] = None,
) -> dict[str, Any]:
    items, total = await audit.list_entries(
        session, principal.organization_id, limit=page.limit, offset=page.offset, action=action
    )
    return {"items": items, "total": total, "limit": page.limit, "offset": page.offset}


@router.get("/roles", summary="Roles and the permissions each grants")
async def roles(_: CurrentPrincipal) -> list[dict[str, Any]]:
    return [
        {"role": r.value, "rank": ROLE_RANK[r], "permissions": sorted(p.value for p in permissions_for(r))}
        for r in sorted(MemberRole, key=ROLE_RANK.__getitem__)
    ]  # fmt: skip
