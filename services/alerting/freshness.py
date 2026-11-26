"""Data-freshness monitoring: degrade stale sources, raise and resolve alerts.

A source is stale when it has an expected update interval and nothing has succeeded for
more than STALE_FACTOR x that interval. Only sources that have been switched on (status
active / degraded / error) are monitored: 'not_configured' sources are not "stale".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from core.logging_config import get_logger

log = get_logger(__name__)

STALE_FACTOR = 2


@dataclass(frozen=True)
class FreshnessChange:
    source_key: str
    change: str  # 'raised' | 'resolved'


def is_stale(last_success_at: datetime | None, interval_s: int, now: datetime) -> bool:
    return last_success_at is None or now - last_success_at > timedelta(
        seconds=STALE_FACTOR * interval_s
    )


def check_freshness(conn: Connection, now: datetime) -> list[FreshnessChange]:
    sources = conn.execute(
        sa.text(
            "SELECT id, key, name, status, last_success_at, expected_update_interval_seconds AS iv "
            "FROM data_sources WHERE expected_update_interval_seconds IS NOT NULL "
            "AND status IN ('active', 'degraded', 'error')"
        )
    ).all()
    changes: list[FreshnessChange] = []
    for s in sources:
        dedupe_key = f"freshness:{s.key}"
        if is_stale(s.last_success_at, s.iv, now):
            if s.status == "active":
                conn.execute(
                    sa.text(
                        "UPDATE data_sources SET status = 'degraded', updated_at = now() WHERE id = :id"
                    ),
                    {"id": s.id},
                )
            raised = conn.execute(
                sa.text(
                    "INSERT INTO system_alerts (severity, category, title, message, data_source_id, "
                    "dedupe_key, raised_at) VALUES ('warning', 'data_freshness', :title, :message, "
                    ":id, :key, :now) ON CONFLICT (dedupe_key) WHERE resolved_at IS NULL DO NOTHING"
                ),
                {
                    "title": f"{s.name} data is stale",
                    "message": (
                        f"No successful update since {s.last_success_at:%Y-%m-%d %H:%M UTC}."
                        if s.last_success_at
                        else "No successful update has been recorded."
                    )
                    + f" Expected every {s.iv} s.",
                    "id": s.id,
                    "key": dedupe_key,
                    "now": now,
                },
            ).rowcount
            if raised:
                changes.append(FreshnessChange(s.key, "raised"))
        else:
            if s.status == "degraded":
                conn.execute(
                    sa.text(
                        "UPDATE data_sources SET status = 'active', updated_at = now() WHERE id = :id"
                    ),
                    {"id": s.id},
                )
            resolved = conn.execute(
                sa.text(
                    "UPDATE system_alerts SET resolved_at = :now WHERE dedupe_key = :key "
                    "AND resolved_at IS NULL"
                ),
                {"key": dedupe_key, "now": now},
            ).rowcount
            if resolved:
                changes.append(FreshnessChange(s.key, "resolved"))
    for c in changes:
        log.info("freshness_alert", source=c.source_key, change=c.change)
    return changes
