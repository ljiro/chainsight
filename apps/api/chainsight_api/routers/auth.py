"""Sign-in, session refresh, sign-out and the current user."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from chainsight_api import ratelimit
from chainsight_api.auth import (
    ACCESS_COOKIE,
    REFRESH_COOKIE,
    REFRESH_PATH,
    CurrentPrincipal,
    client_ip,
    settings_of,
)
from chainsight_api.errors import ApiError
from core.timeutils import utcnow
from database.session import session_scope
from services import audit
from services.auth import crypto
from services.auth import service as auth
from services.auth.rbac import permissions_for

router = APIRouter(prefix="/auth", tags=["auth"])
Session = Annotated[AsyncSession, Depends(session_scope)]


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)
    organization: str | None = Field(None, max_length=100, description="Org slug (optional)")


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


def _set_cookies(request: Request, response: Response, issued: auth.IssuedSession) -> None:
    secure = settings_of(request).cookie_secure
    now = utcnow()
    response.set_cookie(
        ACCESS_COOKIE, issued.access_token, httponly=True, secure=secure, samesite="strict",
        max_age=int((issued.access_expires_at - now).total_seconds()), path="/",
    )  # fmt: skip
    response.set_cookie(
        REFRESH_COOKIE, issued.refresh_token, httponly=True, secure=secure, samesite="strict",
        max_age=int((issued.refresh_expires_at - now).total_seconds()), path=REFRESH_PATH,
    )  # fmt: skip


def _clear_cookies(request: Request, response: Response) -> None:
    secure = settings_of(request).cookie_secure
    response.delete_cookie(ACCESS_COOKIE, path="/", secure=secure, httponly=True, samesite="strict")
    response.delete_cookie(
        REFRESH_COOKIE, path=REFRESH_PATH, secure=secure, httponly=True, samesite="strict"
    )


async def _me(session: AsyncSession, principal: auth.Principal) -> dict[str, Any]:
    org = (
        await session.execute(
            sa.text("SELECT id, name, slug FROM organizations WHERE id = :o"),
            {"o": principal.organization_id},
        )
    ).one()
    user = None
    if principal.user_id:
        u = (
            await session.execute(
                sa.text("SELECT id, email, full_name, last_login_at FROM users WHERE id = :u"),
                {"u": principal.user_id},
            )
        ).one()
        user = dict(u._mapping)
    return {
        "user": user,
        "organization": dict(org._mapping),
        "role": principal.role.value if principal.role else None,
        "permissions": sorted(p.value for p in principal.permissions),
        "auth_method": principal.method,
    }


def _session_body(issued: auth.IssuedSession) -> dict[str, Any]:
    return {
        "role": issued.role.value,
        "permissions": sorted(p.value for p in permissions_for(issued.role)),
        "access_expires_at": issued.access_expires_at,
    }


@router.post("/login", summary="Sign in; sets httpOnly session cookies")
async def login(
    request: Request, response: Response, session: Session, body: LoginRequest
) -> dict[str, Any]:
    settings = settings_of(request)
    ip = client_ip(request)
    if settings.rate_limit_enabled:
        email_key = crypto.sha256_hex(body.email.strip().lower())[:16]
        buckets = [f"login-email:{email_key}"] + ([f"login-ip:{ip}"] if ip else [])
        for bucket in buckets:
            d = await ratelimit.hit(bucket, settings.rate_limit_login_per_minute)
            if not d.allowed:
                raise ApiError(
                    429, "rate_limited", "Too many sign-in attempts; wait a minute",
                    {"retry_after_seconds": d.reset_in},
                )  # fmt: skip
    ua = request.headers.get("user-agent")
    try:
        issued = await auth.login(
            session, settings, email=body.email, password=body.password, now=utcnow(),
            org_slug=body.organization, ip=ip, user_agent=ua,
        )  # fmt: skip
    except auth.AuthError as exc:
        await audit.record(session, "auth.login_failed", ip=ip, user_agent=ua,
                           changes={"email_hash": crypto.sha256_hex(body.email.strip().lower())})  # fmt: skip
        await session.commit()
        raise ApiError(401, exc.code, exc.message) from exc
    await audit.record(
        session, "auth.login", organization_id=issued.organization_id, user_id=issued.user_id,
        entity_type="auth_session", entity_id=issued.session_id, ip=ip, user_agent=ua,
    )  # fmt: skip
    await session.commit()
    _set_cookies(request, response, issued)
    return _session_body(issued)


@router.post("/refresh", summary="Rotate the refresh token and issue a new access token")
async def refresh(request: Request, response: Response, session: Session) -> dict[str, Any]:
    token = request.cookies.get(REFRESH_COOKIE)
    if not token:
        raise ApiError(401, "session_expired", "Your session has ended; sign in again")
    try:
        issued = await auth.refresh(
            session, settings_of(request), refresh_token=token, now=utcnow()
        )
    except auth.AuthError as exc:
        await session.commit()
        _clear_cookies(request, response)
        raise ApiError(401, exc.code, exc.message) from exc
    await session.commit()
    _set_cookies(request, response, issued)
    return _session_body(issued)


@router.post("/logout", status_code=204, summary="Sign out (revokes this session)")
async def logout(request: Request, session: Session) -> Response:
    response = Response(status_code=204)
    token = request.cookies.get(REFRESH_COOKIE)
    if token:
        row = (
            await session.execute(
                sa.text("SELECT id, user_id, organization_id FROM auth_sessions "
                        "WHERE refresh_hash = :h AND revoked_at IS NULL"),
                {"h": crypto.sha256_hex(token)},
            )
        ).one_or_none()  # fmt: skip
        if row:
            await auth.revoke_session(session, row.id, utcnow())
            await audit.record(
                session, "auth.logout", organization_id=row.organization_id, user_id=row.user_id,
                entity_type="auth_session", entity_id=row.id, ip=client_ip(request),
            )  # fmt: skip
            await session.commit()
    _clear_cookies(request, response)
    return response


@router.get("/me", summary="The signed-in user (or API key), organization and permissions")
async def me(principal: CurrentPrincipal, session: Session) -> dict[str, Any]:
    return await _me(session, principal)


@router.post("/password", status_code=204, summary="Change your password (ends other sessions)")
async def change_password(
    request: Request, principal: CurrentPrincipal, session: Session, body: PasswordChange
) -> Response:
    if principal.user_id is None:
        raise ApiError(403, "forbidden", "API keys cannot change passwords")
    current = (
        await session.execute(
            sa.text("SELECT email, password_hash FROM users WHERE id = :u"),
            {"u": principal.user_id},
        )
    ).one()
    if not crypto.verify_password(current.password_hash, body.current_password):
        raise ApiError(400, "invalid_credentials", "Current password is incorrect")
    problems = crypto.password_problems(body.new_password, current.email)
    if problems:
        raise ApiError(422, "weak_password", "Password " + "; ".join(problems))
    now: datetime = utcnow()
    await session.execute(
        sa.text("UPDATE users SET password_hash = :h, updated_at = :now WHERE id = :u"),
        {"h": crypto.hash_password(body.new_password), "now": now, "u": principal.user_id},
    )
    await auth.revoke_user_sessions(
        session, principal.user_id, now, except_session=principal.session_id
    )
    await audit.record(session, "auth.password_changed", principal=principal,
                       entity_type="user", entity_id=principal.user_id, ip=client_ip(request))  # fmt: skip
    await session.commit()
    return Response(status_code=204)
