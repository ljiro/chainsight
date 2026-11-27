"""Request authentication and authorization dependencies.

Credentials, in order:
1. `X-API-Key: cs_...` or `Authorization: Bearer cs_...` (API keys, for programmatic use);
2. `Authorization: Bearer <JWT>`;
3. the `cs_access` cookie set by `/auth/login` (the web app).

Cookie-authenticated unsafe requests must also send `X-Requested-With: chainsight`. Browsers
only attach custom headers to cross-site requests after a CORS preflight, which the API's
CORS policy refuses for foreign origins, so this blocks CSRF even beyond SameSite=Strict.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Request, Response, WebSocket
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import HTTPConnection

from chainsight_api import ratelimit
from chainsight_api.errors import ApiError
from chainsight_api.metrics import AUTH_EVENTS, RATE_LIMITED
from core.settings import Settings
from core.timeutils import utcnow
from database.session import get_sessionmaker, session_scope
from services.auth import crypto
from services.auth.rbac import Permission
from services.auth.service import AuthError, Principal, api_key_principal, session_principal

ACCESS_COOKIE = "cs_access"
REFRESH_COOKIE = "cs_refresh"
CSRF_HEADER = "X-Requested-With"
CSRF_VALUE = "chainsight"
REFRESH_PATH = "/api/v1/auth"
_UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


def settings_of(request: HTTPConnection) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def client_ip(request: HTTPConnection) -> str | None:
    """The caller's address. X-Forwarded-For is honoured only for configured trusted proxies."""
    hops = settings_of(request).trusted_proxy_count
    candidate = request.client.host if request.client else None
    if hops:
        forwarded = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",")]
        forwarded = [p for p in forwarded if p]
        if len(forwarded) >= hops:
            candidate = forwarded[-hops]
    try:
        return str(ipaddress.ip_address(candidate)) if candidate else None
    except ValueError:
        return None  # not an address (test clients, garbage headers): never stored


def _unauthorized(code: str, message: str) -> ApiError:
    return ApiError(401, code, message)


async def _resolve(request: HTTPConnection, session: AsyncSession) -> Principal:
    settings = settings_of(request)
    header_key = request.headers.get("x-api-key")
    auth = request.headers.get("authorization", "")
    bearer = auth[7:].strip() if auth[:7].lower() == "bearer " else None
    try:
        if header_key or (bearer and bearer.startswith(crypto.API_KEY_PREFIX)):
            principal = await api_key_principal(session, header_key or bearer or "", utcnow())
            await session.commit()  # persist last_used_at
            return principal
        token, from_cookie = bearer, False
        if token is None:
            token, from_cookie = request.cookies.get(ACCESS_COOKIE), True
        if not token:
            raise _unauthorized("not_authenticated", "Sign in or provide an API key")
        if (
            from_cookie
            and request.scope.get("method") in _UNSAFE
            and request.headers.get(CSRF_HEADER) != CSRF_VALUE
        ):
            raise ApiError(403, "csrf_check_failed", f"Missing {CSRF_HEADER} header")
        claims = crypto.decode_access_token(settings, token)
        return await session_principal(session, claims)
    except crypto.InvalidToken as exc:
        AUTH_EVENTS.labels(exc.code).inc()
        message = "Access token expired" if exc.code == "token_expired" else "Invalid token"
        raise _unauthorized(exc.code, message) from exc
    except AuthError as exc:
        raise _unauthorized(exc.code, exc.message) from exc


async def current_principal(
    request: Request, response: Response, session: Annotated[AsyncSession, Depends(session_scope)]
) -> Principal:
    cached: Principal | None = getattr(request.state, "principal", None)
    if cached is not None:
        return cached
    principal = await _resolve(request, session)
    request.state.principal = principal
    settings = settings_of(request)
    if settings.rate_limit_enabled:
        d = await ratelimit.hit(principal.subject, settings.rate_limit_per_minute)
        if not d.allowed:
            RATE_LIMITED.labels("principal").inc()
            raise ApiError(
                429, "rate_limited", "Too many requests; slow down",
                {"limit": d.limit, "retry_after_seconds": d.reset_in},
            )  # fmt: skip
    return principal


CurrentPrincipal = Annotated[Principal, Depends(current_principal)]


def require(permission: Permission) -> Callable[..., Awaitable[Principal]]:
    async def dependency(principal: CurrentPrincipal) -> Principal:
        if not principal.can(permission):
            raise ApiError(
                403, "forbidden", "Your role does not allow this action",
                {"required_permission": permission.value},
            )  # fmt: skip
        return principal

    dependency.__name__ = f"require_{permission.name.lower()}"
    return dependency


ReadPrincipal = Annotated[Principal, Depends(require(Permission.DATA_READ))]


async def websocket_principal(ws: WebSocket) -> Principal | None:
    """Authenticate a WebSocket handshake (cookie, bearer token or API key); None if denied."""
    try:
        async with get_sessionmaker()() as session:
            principal = await _resolve(ws, session)
    except ApiError:
        return None
    return principal if principal.can(Permission.DATA_READ) else None
