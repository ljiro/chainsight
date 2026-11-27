"""Append-only audit trail (`audit_logs`; UPDATE and DELETE are blocked by a trigger).

Never put secrets in `changes`: record *that* a key or password changed, not its value.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from services.auth.service import Principal

_INSERT = sa.text(
    "INSERT INTO audit_logs (organization_id, actor_user_id, actor_api_key_id, action, "
    "entity_type, entity_id, ip_address, user_agent, changes) VALUES (:org, :user, :key, "
    ":action, :etype, :eid, CAST(:ip AS inet), :ua, CAST(:changes AS jsonb))"
)


async def record(
    session: AsyncSession,
    action: str,
    *,
    principal: Principal | None = None,
    organization_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    changes: dict[str, Any] | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> None:
    await session.execute(
        _INSERT,
        {
            "org": organization_id or (principal.organization_id if principal else None),
            "user": user_id or (principal.user_id if principal else None),
            "key": principal.api_key_id if principal else None,
            "action": action, "etype": entity_type, "eid": entity_id, "ip": ip,
            "ua": (user_agent or "")[:500] or None,
            "changes": json.dumps(changes or {}, default=str),
        },
    )  # fmt: skip


async def list_entries(
    session: AsyncSession,
    organization_id: uuid.UUID,
    *,
    limit: int,
    offset: int,
    action: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    where = "a.organization_id = :org AND (CAST(:action AS text) IS NULL OR a.action LIKE :prefix)"
    params = {"org": organization_id, "action": action, "prefix": f"{action}%"}
    total = (
        await session.execute(sa.text(f"SELECT count(*) FROM audit_logs a WHERE {where}"), params)
    ).scalar_one()
    rows = await session.execute(
        sa.text(
            "SELECT a.id, a.occurred_at, a.action, a.entity_type, a.entity_id, "
            "host(a.ip_address) AS ip_address, a.changes, u.email AS actor_email, "
            "k.name AS actor_api_key FROM audit_logs a "
            "LEFT JOIN users u ON u.id = a.actor_user_id "
            f"LEFT JOIN api_keys k ON k.id = a.actor_api_key_id WHERE {where} "
            "ORDER BY a.occurred_at DESC, a.id LIMIT :limit OFFSET :offset"
        ),
        params | {"limit": limit, "offset": offset},
    )
    return [dict(r._mapping) for r in rows], int(total)
