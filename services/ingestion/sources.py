"""Data-source bookkeeping shared by every ingestion runner (provenance + freshness)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession

from core.timeutils import utcnow


class UnknownDataSource(LookupError):
    pass


_SELECT_SOURCE = sa.text("SELECT id, data_mode FROM data_sources WHERE key = :key")
_MARK_SUCCESS = sa.text(
    "UPDATE data_sources SET status = 'active', last_success_at = :at, updated_at = now() "
    "WHERE id = :id"
)
_MARK_ERROR = sa.text(
    "UPDATE data_sources SET status = 'error', last_error_at = :at, "
    "last_error_message = :message, updated_at = now() WHERE id = :id"
)
_INSERT_LOG = sa.text(
    "INSERT INTO ingestion_logs (data_source_id, started_at, finished_at, status, "
    "records_received, records_accepted, records_rejected, records_duplicate, error_message, "
    "details) VALUES (:source_id, :started_at, :finished_at, :status, :received, :accepted, "
    ":rejected, :duplicate, :error, CAST(:details AS jsonb)) RETURNING id"
)


def source_info_sync(conn: Connection, key: str) -> tuple[uuid.UUID, str]:
    row = conn.execute(_SELECT_SOURCE, {"key": key}).one_or_none()
    if row is None:
        raise UnknownDataSource(key)
    return row.id, row.data_mode


async def source_info(session: AsyncSession, key: str) -> tuple[uuid.UUID, str]:
    row = (await session.execute(_SELECT_SOURCE, {"key": key})).one_or_none()
    if row is None:
        raise UnknownDataSource(key)
    return row.id, row.data_mode


def _log_params(
    source_id: uuid.UUID,
    started_at: datetime,
    *,
    status: str,
    received: int = 0,
    accepted: int = 0,
    rejected: int = 0,
    duplicate: int = 0,
    error: str | None = None,
    details: dict[str, Any] | None = None,
    finished_at: datetime | None = None,
) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "started_at": started_at,
        "finished_at": finished_at or utcnow(),
        "status": status,
        "received": received,
        "accepted": accepted,
        "rejected": rejected,
        "duplicate": duplicate,
        "error": (error or "")[:2000] or None,
        "details": json.dumps(details or {}, default=str),
    }


def write_log_sync(
    conn: Connection, source_id: uuid.UUID, started_at: datetime, **kw: Any
) -> uuid.UUID:
    log_id: uuid.UUID = conn.execute(
        _INSERT_LOG, _log_params(source_id, started_at, **kw)
    ).scalar_one()
    return log_id


async def write_log(
    session: AsyncSession, source_id: uuid.UUID, started_at: datetime, **kw: Any
) -> uuid.UUID:
    result = await session.execute(_INSERT_LOG, _log_params(source_id, started_at, **kw))
    log_id: uuid.UUID = result.scalar_one()
    return log_id


def mark_success_sync(conn: Connection, source_id: uuid.UUID, at: datetime | None = None) -> None:
    conn.execute(_MARK_SUCCESS, {"id": source_id, "at": at or utcnow()})


async def mark_success(
    session: AsyncSession, source_id: uuid.UUID, at: datetime | None = None
) -> None:
    await session.execute(_MARK_SUCCESS, {"id": source_id, "at": at or utcnow()})


async def mark_error(session: AsyncSession, source_id: uuid.UUID, message: str) -> None:
    await session.execute(_MARK_ERROR, {"id": source_id, "at": utcnow(), "message": message[:1000]})


def mark_error_sync(conn: Connection, source_id: uuid.UUID, message: str) -> None:
    # Error messages are stored for operators but never returned by the public API.
    conn.execute(_MARK_ERROR, {"id": source_id, "at": utcnow(), "message": message[:1000]})
