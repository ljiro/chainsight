"""UTC time helpers. All timestamps inside ChainSight are timezone-aware UTC."""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)


def ensure_utc(value: datetime, *, assume_utc_if_naive: bool = False) -> datetime:
    """Normalize a datetime to UTC.

    Naive datetimes are rejected unless the caller explicitly opts in, because silently
    assuming a timezone is a common source of wrong ETAs and look-ahead bias.
    """
    if value.tzinfo is None or value.utcoffset() is None:
        if not assume_utc_if_naive:
            raise ValueError("naive datetime; source timezone is unknown")
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
