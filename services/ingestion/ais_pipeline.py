"""AIS ingestion pipeline: parse -> dedup -> resolve -> persist -> detect -> publish.

Provider-agnostic: works the same for AISStream, replay, and the simulator. All writes
for a flush happen in one transaction; live updates are published only after commit.
"""

from __future__ import annotations

import json
import uuid
from collections import Counter, OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.logging_config import get_logger
from core.timeutils import utcnow
from integrations.ais.messages import AisPositionReport, AisRejected, AisStaticReport, parse_message
from integrations.ais.providers import RawAisMessage
from services.fleet_tracking.port_calls import Fix, process_port_calls
from services.ingestion.sources import mark_error, mark_success, source_info, write_log
from services.vessel_registry.quality import quality_flags
from services.vessel_registry.resolution import VesselState, apply_static_reports, resolve_vessels

log = get_logger(__name__)

LIVE_CHANNEL = "ais:positions"
Publisher = Callable[[str], Awaitable[Any]]

_INSERT_POSITIONS = sa.text(
    """
    INSERT INTO vessel_positions (vessel_id, observed_at, source_id, location, sog_knots, cog_deg,
        heading_deg, nav_status, rate_of_turn, position_accuracy_high, message_type, quality_flags)
    SELECT r.vessel_id, r.observed_at, :source_id,
           ST_SetSRID(ST_MakePoint(r.lon, r.lat), 4326)::geography, r.sog, r.cog, r.heading,
           r.nav, r.rot, r.accuracy, r.message_type, r.flags
    FROM jsonb_to_recordset(CAST(:rows AS jsonb)) AS r(
        vessel_id uuid, observed_at timestamptz, lon float8, lat float8, sog real, cog real,
        heading smallint, nav smallint, rot real, accuracy boolean, message_type text,
        flags smallint)
    ON CONFLICT DO NOTHING
    RETURNING vessel_id, observed_at
    """
)
_UPDATE_LATEST = sa.text(
    """
    UPDATE vessels v SET
        last_seen_at = r.observed_at,
        last_location = ST_SetSRID(ST_MakePoint(r.lon, r.lat), 4326)::geography,
        last_sog_knots = r.sog, last_cog_deg = r.cog, last_heading_deg = r.heading,
        last_nav_status = r.nav, last_position_source_id = :source_id,
        name = COALESCE(v.name, r.name),
        first_seen_at = LEAST(COALESCE(v.first_seen_at, r.observed_at), r.observed_at),
        updated_at = now()
    FROM jsonb_to_recordset(CAST(:rows AS jsonb)) AS r(
        vessel_id uuid, observed_at timestamptz, lon float8, lat float8, sog real, cog real,
        heading smallint, nav smallint, name text)
    WHERE v.id = r.vessel_id AND (v.last_seen_at IS NULL OR v.last_seen_at < r.observed_at)
    RETURNING v.id, v.mmsi, v.name, v.vessel_class
    """
)


@dataclass
class WindowStats:
    started_at: datetime
    received: int = 0
    accepted: int = 0
    duplicates: int = 0
    ignored: int = 0
    statics: int = 0
    imo_conflicts: int = 0
    port_events: int = 0
    rejected: Counter[str] = field(default_factory=Counter)
    latency_ms_total: float = 0.0
    flush_errors: int = 0
    last_error: str | None = None

    @property
    def rejected_total(self) -> int:
        return sum(self.rejected.values())


@dataclass(frozen=True)
class FlushResult:
    inserted: int
    duplicates: int
    vessels_updated: int
    port_events: int


class _RecentKeys:
    """Bounded LRU of recently stored (mmsi, observed_at) keys for cheap duplicate drops."""

    def __init__(self, capacity: int = 50_000) -> None:
        self.capacity = capacity
        self._keys: OrderedDict[tuple[int, datetime], None] = OrderedDict()

    def __contains__(self, key: tuple[int, datetime]) -> bool:
        return key in self._keys

    def add(self, key: tuple[int, datetime]) -> None:
        self._keys[key] = None
        self._keys.move_to_end(key)
        while len(self._keys) > self.capacity:
            self._keys.popitem(last=False)


class AisPipeline:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        *,
        source_key: str,
        receiver_type: str,
        publish: Publisher | None = None,
        batch_size: int = 500,
        log_window_s: float = 60.0,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._sessionmaker = sessionmaker
        self.source_key = source_key
        self.receiver_type = receiver_type
        self._publish = publish
        self.batch_size = batch_size
        self.log_window_s = log_window_s
        self._clock = clock
        self.source_id: uuid.UUID | None = None
        self.data_mode: str | None = None
        self._positions: dict[tuple[int, datetime], AisPositionReport] = {}
        self._statics: dict[int, AisStaticReport] = {}
        self._recent = _RecentKeys()
        self._last_fix: dict[int, tuple[datetime, float, float]] = {}
        self.window = WindowStats(started_at=clock())

    async def start(self) -> None:
        async with self._sessionmaker() as session:
            self.source_id, self.data_mode = await source_info(session, self.source_key)

    @property
    def buffered(self) -> int:
        return len(self._positions) + len(self._statics)

    def should_flush(self) -> bool:
        return len(self._positions) >= self.batch_size

    def ingest(self, message: RawAisMessage) -> None:
        self.window.received += 1
        try:
            report = parse_message(message.payload, received_at=message.received_at)
        except AisRejected as exc:
            self.window.rejected[exc.reason] += 1
            return
        if report is None:
            self.window.ignored += 1
            return
        if isinstance(report, AisStaticReport):
            prev = self._statics.get(report.mmsi)
            if prev is None or report.observed_at >= prev.observed_at:
                self._statics[report.mmsi] = report
            self.window.statics += 1
            return
        key = report.dedup_key
        if key in self._positions or key in self._recent:
            self.window.duplicates += 1
            return
        self._positions[key] = report

    async def flush(self) -> FlushResult:
        if self.source_id is None:
            raise RuntimeError("call start() before flush()")
        positions = sorted(self._positions.values(), key=lambda p: p.observed_at)
        statics = list(self._statics.values())
        self._positions, self._statics = {}, {}
        if not positions and not statics:
            return FlushResult(0, 0, 0, 0)

        mmsis = {p.mmsi for p in positions} | {s.mmsi for s in statics}
        names: dict[int, str | None] = {s.mmsi: s.name for s in statics}
        seen_at: dict[int, datetime] = {}
        for p in positions:
            names.setdefault(p.mmsi, p.ship_name)
            seen_at.setdefault(p.mmsi, p.observed_at)
        for s in statics:
            seen_at.setdefault(s.mmsi, s.observed_at)

        try:
            stored, rows, updated, latest, events = await self._persist(
                positions, statics, mmsis, names, seen_at
            )
        except Exception as exc:
            # The batch is lost (transaction rolled back); record it so logs never claim success.
            self.window.flush_errors += 1
            self.window.last_error = f"{type(exc).__name__}: {str(exc)[:300]}"
            log.exception("ais_flush_failed", positions=len(positions), statics=len(statics))
            raise

        now = self._clock()
        for _row, p in stored:
            self._recent.add(p.dedup_key)
            self._last_fix[p.mmsi] = (p.observed_at, p.latitude, p.longitude)
            self.window.latency_ms_total += max(0.0, (now - p.observed_at).total_seconds() * 1000)
        self.window.accepted += len(stored)
        self.window.duplicates += len(rows) - len(stored)
        self.window.port_events += len(events)

        if self._publish and updated:
            await self._publish_live(latest, {r.id: r for r in updated})
        return FlushResult(len(stored), len(rows) - len(stored), len(updated), len(events))

    async def _persist(
        self,
        positions: list[AisPositionReport],
        statics: list[AisStaticReport],
        mmsis: set[int],
        names: dict[int, str | None],
        seen_at: dict[int, datetime],
    ) -> tuple[Any, ...]:
        assert self.source_id is not None
        async with self._sessionmaker() as session, session.begin():
            states = await resolve_vessels(session, mmsis, names, self.source_id, seen_at)
            self.window.imo_conflicts += await apply_static_reports(
                session, statics, states, self.source_id
            )
            rows = self._position_rows(positions, states)
            inserted = (
                await session.execute(
                    _INSERT_POSITIONS, {"rows": json.dumps(rows), "source_id": self.source_id}
                )
            ).all()
            inserted_keys = {(r.vessel_id, r.observed_at) for r in inserted}
            stored = [
                (row, p)
                for row, p in zip(rows, positions, strict=True)
                if (uuid.UUID(row["vessel_id"]), p.observed_at) in inserted_keys
            ]
            latest = self._latest_per_vessel(stored)
            updated = (
                await session.execute(
                    _UPDATE_LATEST,
                    {"rows": json.dumps(list(latest.values())), "source_id": self.source_id},
                )
            ).all()
            events = await process_port_calls(
                session,
                [
                    Fix(
                        uuid.UUID(row["vessel_id"]),
                        p.observed_at,
                        p.latitude,
                        p.longitude,
                        p.sog_knots,
                        p.nav_status,
                    )
                    for row, p in stored
                ],
                self.source_id,
                {s.vessel_id for s in states.values() if s.is_new},
            )

        return stored, rows, updated, latest, events

    def _position_rows(
        self, positions: list[AisPositionReport], states: dict[int, VesselState]
    ) -> list[dict[str, Any]]:
        rows = []
        for p in positions:
            state = states[p.mmsi]
            previous = self._last_fix.get(p.mmsi)
            if previous is None and state.last_seen_at and state.last_lat is not None:
                previous = (state.last_seen_at, state.last_lat, state.last_lon or 0.0)
            flags = quality_flags(
                observed_at=p.observed_at,
                lat=p.latitude,
                lon=p.longitude,
                sog=p.sog_knots,
                cog=p.cog_deg,
                accuracy_high=p.position_accuracy_high,
                previous=previous,
            )
            if previous is None or p.observed_at >= previous[0]:
                self._last_fix[p.mmsi] = (p.observed_at, p.latitude, p.longitude)
            rows.append(
                {
                    "vessel_id": str(state.vessel_id),
                    "observed_at": p.observed_at.isoformat(),
                    "lon": p.longitude,
                    "lat": p.latitude,
                    "sog": p.sog_knots,
                    "cog": p.cog_deg,
                    "heading": p.heading_deg,
                    "nav": p.nav_status,
                    "rot": p.rate_of_turn,
                    "accuracy": p.position_accuracy_high,
                    "message_type": p.message_type,
                    "flags": int(flags),
                    "_name": p.ship_name,
                }
            )
        return rows

    @staticmethod
    def _latest_per_vessel(
        stored: list[tuple[dict[str, Any], AisPositionReport]],
    ) -> dict[str, dict[str, Any]]:
        latest: dict[str, dict[str, Any]] = {}
        for row, _ in stored:  # stored is time-ordered, so the last write wins
            latest[row["vessel_id"]] = {
                k: row[k]
                for k in ("vessel_id", "observed_at", "lon", "lat", "sog", "cog", "heading", "nav")
            } | {"name": row["_name"]}
        return latest

    async def _publish_live(
        self, latest: dict[str, dict[str, Any]], vessels: dict[Any, Any]
    ) -> None:
        assert self._publish is not None
        items = []
        for vid, row in latest.items():
            v = vessels.get(uuid.UUID(vid))
            if v is None:  # not newer than what the vessel already had
                continue
            items.append(
                {
                    "vessel_id": vid,
                    "mmsi": v.mmsi,
                    "name": v.name,
                    "vessel_class": v.vessel_class,
                    "lat": row["lat"],
                    "lon": row["lon"],
                    "sog_knots": row["sog"],
                    "cog_deg": row["cog"],
                    "heading_deg": row["heading"],
                    "nav_status": row["nav"],
                    "observed_at": row["observed_at"],
                }
            )
        if items:
            message = {"source": self.source_key, "data_mode": self.data_mode, "positions": items}
            try:
                await self._publish(json.dumps(message))
            except Exception as exc:  # live fan-out is best effort; storage already succeeded
                log.warning("ais_publish_failed", error_type=type(exc).__name__)

    def window_due(self) -> bool:
        return (self._clock() - self.window.started_at).total_seconds() >= self.log_window_s

    async def close_window(self) -> uuid.UUID | None:
        """Persist ingestion stats for the current window and start a new one."""
        if self.source_id is None:
            return None
        w, now = self.window, self._clock()
        self.window = WindowStats(started_at=now)
        if w.received == 0:
            return None
        status = "succeeded" if w.rejected_total == 0 and not w.flush_errors else "partial"
        if w.accepted == 0 and (w.flush_errors or w.rejected_total == w.received):
            status = "failed"
        async with self._sessionmaker() as session, session.begin():
            log_id = await write_log(
                session,
                self.source_id,
                w.started_at,
                finished_at=now,
                status=status,
                received=w.received,
                accepted=w.accepted,
                rejected=w.rejected_total,
                duplicate=w.duplicates,
                error=w.last_error,
                details={
                    "flush_errors": w.flush_errors,
                    "rejected_by_reason": dict(w.rejected),
                    "ignored_message_types": w.ignored,
                    "static_reports": w.statics,
                    "imo_conflicts": w.imo_conflicts,
                    "port_events": w.port_events,
                },
            )
            await session.execute(
                sa.text(
                    "INSERT INTO vessel_position_sources (ingestion_log_id, data_source_id, "
                    "receiver_type, window_start, window_end, messages, positions, mean_latency_ms) "
                    "VALUES (:log, :src, :rt, :ws, :we, :m, :p, :lat)"
                ),
                {
                    "log": log_id,
                    "src": self.source_id,
                    "rt": self.receiver_type,
                    "ws": w.started_at,
                    "we": now,
                    "m": w.received,
                    "p": w.accepted,
                    "lat": w.latency_ms_total / w.accepted if w.accepted else None,
                },
            )
            if w.flush_errors:
                await mark_error(session, self.source_id, w.last_error or "flush failed")
            elif w.accepted or w.statics:
                await mark_success(session, self.source_id, now)
        log.info(
            "ais_window",
            source=self.source_key,
            received=w.received,
            accepted=w.accepted,
            duplicates=w.duplicates,
            rejected=dict(w.rejected),
            port_events=w.port_events,
        )
        return log_id
