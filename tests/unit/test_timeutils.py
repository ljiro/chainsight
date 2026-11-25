from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from core.timeutils import ensure_utc, utcnow


def test_utcnow_is_aware_utc() -> None:
    assert utcnow().tzinfo is UTC


def test_ensure_utc_converts_offsets() -> None:
    t = datetime(2026, 1, 1, 10, 0, tzinfo=timezone(timedelta(hours=8)))
    assert ensure_utc(t) == datetime(2026, 1, 1, 2, 0, tzinfo=UTC)


def test_ensure_utc_rejects_naive_by_default() -> None:
    with pytest.raises(ValueError):
        ensure_utc(datetime(2026, 1, 1))


def test_ensure_utc_naive_opt_in() -> None:
    assert ensure_utc(datetime(2026, 1, 1), assume_utc_if_naive=True).tzinfo is UTC
