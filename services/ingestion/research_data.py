"""Research data collectors: IMF PortWatch and FRED into point-in-time indicator storage.

Storage rules (see database/models/research.py):
- First load of a series is a bulk history load: each value's `available_at` comes from the
  source's documented, conservative availability rule (`available_at_assumed = true`).
- Later runs re-fetch a trailing window. A new period's `available_at` is the time ChainSight
  first saw it. A changed value is stored as a new revision, available from now. Nothing is
  overwritten, so a backtest can replay exactly what was known at each date.

    uv run python -m services.ingestion.research_data portwatch|fred|all
"""

from __future__ import annotations

import argparse
import json
import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Connection, Engine

from core.logging_config import configure_logging, get_logger
from core.settings import Settings, get_settings
from core.timeutils import utcnow
from database.session import get_sync_engine
from integrations.research import fred, portwatch
from integrations.research.availability import AvailabilityRule
from integrations.research.http import ResearchSourceError
from services.ingestion.sources import (
    mark_error_sync,
    mark_success_sync,
    source_info_sync,
    write_log_sync,
)

log = get_logger(__name__)
REVISION_WINDOW_DAYS = 60  # re-fetched each run so late revisions are captured
REL_TOL = 1e-9


@dataclass(frozen=True)
class SeriesSpec:
    key: str
    category: str  # maritime | market
    name: str
    entity_kind: str
    entity_key: str
    entity_name: str
    metric: str
    unit: str | None
    rule: AvailabilityRule
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass
class StoreResult:
    inserted: int = 0
    revised: int = 0
    unchanged: int = 0


def _day_ts(d: date) -> datetime:
    return datetime.combine(d, time(0), UTC)


def ensure_series(conn: Connection, source_id: uuid.UUID, spec: SeriesSpec) -> uuid.UUID:
    attrs = spec.attributes | {"availability_rule": spec.rule.as_dict()}
    lag = getattr(spec.rule, "min_lag_days", None)
    lag = lag if lag is not None else 1 + getattr(spec.rule, "lag_days", 0)
    series_id: uuid.UUID = conn.execute(
        sa.text(
            """
            INSERT INTO indicator_series (key, source_id, category, name, entity_kind, entity_key,
                entity_name, metric, unit, publication_lag_days, attributes)
            VALUES (:key, :src, :category, :name, :kind, :ekey, :ename, :metric, :unit, :lag,
                CAST(:attrs AS jsonb))
            ON CONFLICT (key) DO UPDATE SET name = EXCLUDED.name, entity_name = EXCLUDED.entity_name,
                unit = EXCLUDED.unit, publication_lag_days = EXCLUDED.publication_lag_days,
                attributes = EXCLUDED.attributes, updated_at = now()
            RETURNING id
            """
        ),
        {"key": spec.key, "src": source_id, "category": spec.category, "name": spec.name,
         "kind": spec.entity_kind, "ekey": spec.entity_key, "ename": spec.entity_name,
         "metric": spec.metric, "unit": spec.unit, "lag": lag, "attrs": json.dumps(attrs)},
    ).scalar_one()  # fmt: skip
    return series_id


def store_observations(
    conn: Connection,
    series_id: uuid.UUID,
    points: Iterable[tuple[date, float]],
    rule: AvailabilityRule,
    now: datetime,
) -> StoreResult:
    latest = {
        r.as_of: (r.revision, r.value)
        for r in conn.execute(
            sa.text(
                "SELECT DISTINCT ON (as_of) as_of, revision, value FROM indicator_observations "
                "WHERE series_id = :s ORDER BY as_of, revision DESC"
            ),
            {"s": series_id},
        )
    }
    bulk = not latest  # first load: history with rule-based availability
    result, rows = StoreResult(), []
    for day, value in points:
        as_of = _day_ts(day)
        prev = latest.get(as_of)
        if prev is None:
            assumed = bulk
            rows.append({"as_of": as_of.isoformat(), "revision": 0, "value": value,
                         "available_at": (rule.available_at(day) if bulk else now).isoformat(),
                         "assumed": assumed})  # fmt: skip
            latest[as_of] = (0, value)
            result.inserted += 1
        elif abs(prev[1] - value) > REL_TOL * max(1.0, abs(prev[1])):
            rows.append({"as_of": as_of.isoformat(), "revision": prev[0] + 1, "value": value,
                         "available_at": now.isoformat(), "assumed": False})  # fmt: skip
            latest[as_of] = (prev[0] + 1, value)
            result.revised += 1
        else:
            result.unchanged += 1
    for i in range(0, len(rows), 5000):
        conn.execute(
            sa.text(
                """
                INSERT INTO indicator_observations (series_id, as_of, revision, value, available_at,
                    available_at_assumed)
                SELECT :s, r.as_of, r.revision, r.value, r.available_at, r.assumed
                FROM jsonb_to_recordset(CAST(:rows AS jsonb)) AS r(as_of timestamptz, revision smallint,
                    value double precision, available_at timestamptz, assumed boolean)
                ON CONFLICT DO NOTHING
                """
            ),
            {"s": series_id, "rows": json.dumps(rows[i : i + 5000])},
        )
    return result


def _fetch_start(conn: Connection, prefix: str, history_start: date) -> date:
    last = conn.execute(
        sa.text(
            "SELECT min(m) FROM (SELECT max(o.as_of) AS m FROM indicator_series s "
            "JOIN indicator_observations o ON o.series_id = s.id WHERE s.key LIKE :p "
            "GROUP BY s.id) x"
        ),
        {"p": prefix + "%"},
    ).scalar()
    if last is None:
        return history_start
    resume: date = last.date() - timedelta(days=REVISION_WINDOW_DAYS)
    return max(history_start, resume)


def portwatch_specs(record: portwatch.DailyRecord) -> list[tuple[SeriesSpec, float]]:
    out = []
    for metric, value in record.metrics.items():
        unit, desc = portwatch.METRICS[record.kind][metric]
        out.append(
            (
                SeriesSpec(
                    key=f"portwatch:{record.kind}:{record.entity_id}:{metric}",
                    category="maritime",
                    name=f"{record.entity_name}: {desc}",
                    entity_kind=record.kind,
                    entity_key=record.entity_id,
                    entity_name=record.entity_name,
                    metric=metric,
                    unit=unit,
                    rule=portwatch.AVAILABILITY[record.kind],
                    attributes={"country_iso3": record.country_iso3, "estimated": True},
                ),
                value,
            )
        )
    return out


def _store_grouped(
    conn: Connection,
    source_id: uuid.UUID,
    grouped: dict[str, tuple[SeriesSpec, list[tuple[date, float]]]],
    now: datetime,
) -> StoreResult:
    total = StoreResult()
    for spec, points in grouped.values():
        sid = ensure_series(conn, source_id, spec)
        r = store_observations(conn, sid, points, spec.rule, now)
        total.inserted += r.inserted
        total.revised += r.revised
        total.unchanged += r.unchanged
    return total


def collect_portwatch(
    engine: Engine,
    settings: Settings,
    *,
    client: portwatch.PortWatchClient | None = None,
    now: datetime | None = None,
    ports: Sequence[str] | None = None,
) -> StoreResult:
    now = now or utcnow()
    client = client or portwatch.PortWatchClient()
    started = utcnow()
    ports = list(ports or settings.research_portwatch_ports or portwatch.DEFAULT_PORTS)
    history_start = date.fromisoformat(settings.research_history_start)
    with engine.begin() as conn:
        source_id, _ = source_info_sync(conn, "imf-portwatch")
    total, received = StoreResult(), 0
    try:
        for kind, entities in (
            ("port", ports),
            ("chokepoint", sorted(client.entities("chokepoint"))),
        ):
            with engine.connect() as conn:
                start = _fetch_start(conn, f"portwatch:{kind}:", history_start)
            grouped: dict[str, tuple[SeriesSpec, list[tuple[date, float]]]] = {}
            for rec in client.daily(kind, entities, start):
                received += 1
                for spec, value in portwatch_specs(rec):
                    grouped.setdefault(spec.key, (spec, []))[1].append((rec.day, value))
            with engine.begin() as conn:
                r = _store_grouped(conn, source_id, grouped, now)
            total.inserted += r.inserted
            total.revised += r.revised
            total.unchanged += r.unchanged
            log.info(
                "portwatch_collected", kind=kind, start=str(start), records=received, **r.__dict__
            )
    except ResearchSourceError as exc:
        with engine.begin() as conn:
            mark_error_sync(conn, source_id, str(exc))
            write_log_sync(
                conn, source_id, started, status="failed", received=received, error=str(exc)
            )
        raise
    with engine.begin() as conn:
        mark_success_sync(conn, source_id, now)
        write_log_sync(conn, source_id, started, status="succeeded", received=received,
                       accepted=total.inserted + total.revised, duplicate=total.unchanged,
                       details={"revised": total.revised})  # fmt: skip
    return total


def collect_fred(
    engine: Engine,
    settings: Settings,
    *,
    client: fred.FredClient | None = None,
    now: datetime | None = None,
) -> StoreResult:
    now = now or utcnow()
    key = settings.fred_api_key.get_secret_value() if settings.fred_api_key else None
    client = client or fred.FredClient(key)
    started = utcnow()
    catalog = {s.series_id: s for s in fred.DEFAULT_SERIES}
    wanted = settings.research_fred_series or list(catalog)
    history_start = date.fromisoformat(settings.research_history_start)
    with engine.begin() as conn:
        source_id, _ = source_info_sync(conn, "fred")
    total, received = StoreResult(), 0
    try:
        for sid in wanted:
            meta = catalog.get(sid, fred.FredSeries(sid, sid, "", "see FRED series notes"))
            spec = SeriesSpec(
                key=f"fred:{sid}", category="market", name=meta.name, entity_kind="asset",
                entity_key=sid, entity_name=meta.name, metric="value", unit=meta.unit or None,
                rule=fred.AVAILABILITY,
                attributes={"original_source": meta.original_source, "download_method": client.method},
            )  # fmt: skip
            with engine.connect() as conn:
                start = _fetch_start(conn, spec.key, history_start)
            points = client.observations(sid, start)
            received += len(points)
            with engine.begin() as conn:
                r = _store_grouped(conn, source_id, {spec.key: (spec, points)}, now)
            total.inserted += r.inserted
            total.revised += r.revised
            total.unchanged += r.unchanged
    except ResearchSourceError as exc:
        with engine.begin() as conn:
            mark_error_sync(conn, source_id, str(exc))
            write_log_sync(
                conn, source_id, started, status="failed", received=received, error=str(exc)
            )
        raise
    with engine.begin() as conn:
        mark_success_sync(conn, source_id, now)
        write_log_sync(conn, source_id, started, status="succeeded", received=received,
                       accepted=total.inserted + total.revised, duplicate=total.unchanged,
                       details={"revised": total.revised, "method": client.method})  # fmt: skip
    return total


def main(argv: list[str] | None = None) -> int:
    s = get_settings()
    configure_logging(s.log_level, s.log_format)
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("source", choices=["portwatch", "fred", "all"])
    args = parser.parse_args(argv)
    engine = get_sync_engine()
    out: dict[str, Any] = defaultdict(dict)
    if args.source in ("fred", "all"):
        out["fred"] = collect_fred(engine, s).__dict__
    if args.source in ("portwatch", "all"):
        out["portwatch"] = collect_portwatch(engine, s).__dict__
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
