"""When a published data point could first have been known.

Bulk-loaded history has no record of when each value was first published, so an explicit,
conservative rule assigns `available_at`. Values ingested live use the actual time ChainSight
first saw them instead. A rule that is too early creates look-ahead bias; a rule that is too
late only makes research slightly staler, so every rule errs late.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any


@dataclass(frozen=True)
class WeeklyRelease:
    """Published once a week on `weekday` (0 = Monday) at `hour` UTC, covering days at least
    `min_lag_days` old, plus `safety_days` in case a release is skipped or late."""

    weekday: int
    hour: int
    min_lag_days: int
    safety_days: int = 7
    kind: str = "weekly_release"

    def available_at(self, day: date) -> datetime:
        earliest = day + timedelta(days=self.min_lag_days)
        release = earliest + timedelta(days=(self.weekday - earliest.weekday()) % 7)
        return datetime.combine(release, time(self.hour), UTC) + timedelta(days=self.safety_days)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class NextDay:
    """Known by the end of the observation day (e.g. a market closing price): available from
    00:00 UTC of the following day, plus `lag_days`."""

    lag_days: int = 0
    kind: str = "next_day"

    def available_at(self, day: date) -> datetime:
        return datetime.combine(day + timedelta(days=1 + self.lag_days), time(0), UTC)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


AvailabilityRule = WeeklyRelease | NextDay


def rule_from_dict(d: dict[str, Any]) -> AvailabilityRule:
    params = {k: v for k, v in d.items() if k != "kind"}
    if d["kind"] == "weekly_release":
        return WeeklyRelease(**params)
    if d["kind"] == "next_day":
        return NextDay(**params)
    raise ValueError(f"unknown availability rule {d['kind']!r}")
