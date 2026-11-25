"""Readiness probes. Each probe is an async callable returning `ComponentHealth`.

Probes are injected via `get_readiness_probes` so tests can substitute fakes.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from functools import lru_cache
from pathlib import Path

import redis.asyncio as aioredis
import sqlalchemy as sa
from alembic.script import ScriptDirectory

from chainsight_api.schemas.health import ComponentHealth, ComponentState
from core.logging_config import get_logger
from core.settings import get_settings
from database.session import get_async_engine

log = get_logger(__name__)

Probe = Callable[[], Awaitable[ComponentHealth]]

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "database" / "migrations"


@lru_cache
def expected_migration_head() -> str | None:
    try:
        return ScriptDirectory(str(MIGRATIONS_DIR)).get_current_head()
    except Exception:  # pragma: no cover - only if the source tree is incomplete
        log.warning("migration_head_unresolved", path=str(MIGRATIONS_DIR))
        return None


async def probe_database() -> ComponentHealth:
    start = time.perf_counter()
    try:
        async with get_async_engine().connect() as conn:
            await conn.execute(sa.text("SELECT 1"))
            ext_rows = await conn.execute(
                sa.text(
                    "SELECT extname, extversion FROM pg_extension "
                    "WHERE extname IN ('postgis', 'timescaledb')"
                )
            )
            extensions = {row.extname: row.extversion for row in ext_rows}
            has_version_table = (
                await conn.execute(sa.text("SELECT to_regclass('public.alembic_version')"))
            ).scalar() is not None
            revision = (
                (await conn.execute(sa.text("SELECT version_num FROM alembic_version"))).scalar()
                if has_version_table
                else None
            )
    except Exception as exc:
        return ComponentHealth(
            name="database",
            status="error",
            latency_ms=_ms(start),
            detail={"error": type(exc).__name__},
        )

    head = expected_migration_head()
    status: ComponentState = "ok"
    issues: list[str] = []
    if "postgis" not in extensions:
        status, issues = "degraded", [*issues, "postgis extension missing"]
    if head is not None and revision != head:
        status, issues = "degraded", [*issues, "migrations pending"]
    return ComponentHealth(
        name="database",
        status=status,
        latency_ms=_ms(start),
        detail={
            "postgis": extensions.get("postgis"),
            "timescaledb": extensions.get("timescaledb"),
            "migration_revision": revision,
            "migration_head": head,
            "issues": issues,
        },
    )


_redis: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(  # type: ignore[no-untyped-call]
            str(get_settings().redis_url), socket_connect_timeout=2, socket_timeout=2
        )
    return _redis


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None


async def probe_redis() -> ComponentHealth:
    start = time.perf_counter()
    try:
        await get_redis().ping()
    except Exception as exc:
        return ComponentHealth(
            name="redis",
            status="error",
            latency_ms=_ms(start),
            detail={"error": type(exc).__name__},
        )
    return ComponentHealth(name="redis", status="ok", latency_ms=_ms(start))


def get_readiness_probes() -> list[Probe]:
    return [probe_database, probe_redis]


def overall_status(components: list[ComponentHealth]) -> ComponentState:
    states = {c.status for c in components}
    if "error" in states:
        return "error"
    if "degraded" in states:
        return "degraded"
    return "ok"


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 2)
