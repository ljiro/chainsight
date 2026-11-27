"""Roles and the permissions they grant (per organization).

Each role includes everything the role below it can do:
viewer < analyst < admin < owner.
"""

from __future__ import annotations

from enum import StrEnum

from database.enums import MemberRole


class Permission(StrEnum):
    DATA_READ = "data:read"  # fleet, ports, predictions, research, the org's shipments
    SHIPMENTS_WRITE = "shipments:write"  # track, refresh, CSV import
    RESEARCH_WRITE = "research:write"  # create and run experiments
    MEMBERS_MANAGE = "members:manage"
    API_KEYS_MANAGE = "api_keys:manage"
    INTEGRATIONS_MANAGE = "integrations:manage"
    AUDIT_READ = "audit:read"
    ORG_MANAGE = "org:manage"  # rename the org, grant or revoke the owner role


_VIEWER = frozenset({Permission.DATA_READ})
_ANALYST = _VIEWER | {Permission.SHIPMENTS_WRITE, Permission.RESEARCH_WRITE}
_ADMIN = _ANALYST | {
    Permission.MEMBERS_MANAGE,
    Permission.API_KEYS_MANAGE,
    Permission.INTEGRATIONS_MANAGE,
    Permission.AUDIT_READ,
}
_OWNER = _ADMIN | {Permission.ORG_MANAGE}

ROLE_PERMISSIONS: dict[MemberRole, frozenset[Permission]] = {
    MemberRole.VIEWER: _VIEWER,
    MemberRole.ANALYST: _ANALYST,
    MemberRole.ADMIN: _ADMIN,
    MemberRole.OWNER: _OWNER,
}

ROLE_RANK = {MemberRole.VIEWER: 0, MemberRole.ANALYST: 1, MemberRole.ADMIN: 2, MemberRole.OWNER: 3}


def permissions_for(role: MemberRole) -> frozenset[Permission]:
    return ROLE_PERMISSIONS[role]


def can_assign(actor: MemberRole, target: MemberRole) -> bool:
    """Admins may assign roles up to admin; only owners may grant or revoke owner."""
    if target is MemberRole.OWNER:
        return actor is MemberRole.OWNER
    return ROLE_RANK[actor] >= ROLE_RANK[MemberRole.ADMIN] and ROLE_RANK[actor] >= ROLE_RANK[target]


def parse_scopes(scopes: list[str]) -> frozenset[Permission]:
    """API key scopes are permission names; unknown entries are ignored (fail closed)."""
    valid = {p.value for p in Permission}
    return frozenset(Permission(s) for s in scopes if s in valid)
