"""Import the UN/LOCODE seaport registry into `ports` and build circular geofences.

    uv run python -m services.ingestion.port_registry            # download (default URL)
    uv run python -m services.ingestion.port_registry --file code-list.csv
    uv run python -m services.ingestion.port_registry --radius-m 6000

The data is downloaded at import time and never committed to this repository.
Re-running is idempotent: ports are upserted by UN/LOCODE and geofences rebuilt.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from itertools import islice
from pathlib import Path

import httpx
import sqlalchemy as sa
from sqlalchemy.engine import Engine

from core.logging_config import configure_logging, get_logger
from core.settings import get_settings
from core.timeutils import utcnow
from database.session import get_sync_engine
from integrations.ports.unlocode import PortRecord, iter_seaports
from services.ingestion.sources import mark_success_sync, source_info_sync, write_log_sync

log = get_logger(__name__)

SOURCE_KEY = "un-locode"
BATCH = 1000

_UPSERT = sa.text(
    """
    INSERT INTO ports (unlocode, name, country_code, location, source_id, source_ref)
    SELECT r.unlocode, r.name, r.country_code,
           ST_SetSRID(ST_MakePoint(r.lon, r.lat), 4326)::geography, :source_id,
           'function=' || r.functions
    FROM jsonb_to_recordset(CAST(:rows AS jsonb))
         AS r(unlocode text, name text, country_code text, lon float8, lat float8, functions text)
    ON CONFLICT (unlocode) DO UPDATE SET
        name = EXCLUDED.name,
        country_code = EXCLUDED.country_code,
        location = EXCLUDED.location,
        source_id = EXCLUDED.source_id,
        source_ref = EXCLUDED.source_ref,
        updated_at = now()
    """
)
# Ports this source no longer lists. Unreferenced ones are removed; referenced ones are
# kept (calls/voyages point at them) and reported so an operator can decide.
_DELETE_STALE = sa.text(
    """
    DELETE FROM ports p WHERE p.source_id = :source_id AND NOT (p.unlocode = ANY(:codes))
      AND NOT EXISTS (SELECT 1 FROM voyage_port_calls c WHERE c.port_id = p.id)
      AND NOT EXISTS (SELECT 1 FROM voyages v WHERE p.id IN (v.origin_port_id, v.destination_port_id))
      AND NOT EXISTS (SELECT 1 FROM maritime_routes r WHERE p.id IN (r.origin_port_id, r.destination_port_id))
      AND NOT EXISTS (SELECT 1 FROM weather_observations w WHERE w.port_id = p.id)
    """
)
_STALE_REFERENCED = sa.text(
    "SELECT unlocode FROM ports WHERE source_id = :source_id AND NOT (unlocode = ANY(:codes))"
)
_DELETE_AREAS = sa.text(
    "DELETE FROM port_areas WHERE method = 'circle_buffer' "
    "AND port_id IN (SELECT id FROM ports WHERE source_id = :source_id)"
)
_INSERT_AREAS = sa.text(
    "INSERT INTO port_areas (port_id, area, radius_m, method) "
    "SELECT id, ST_Buffer(location, :radius), :radius, 'circle_buffer' "
    "FROM ports WHERE source_id = :source_id"
)


@dataclass(frozen=True)
class ImportResult:
    ports: int
    areas: int
    removed: int = 0
    stale_referenced: tuple[str, ...] = ()


def _chunks(records: Iterable[PortRecord], size: int) -> Iterator[list[PortRecord]]:
    it = iter(records)
    while chunk := list(islice(it, size)):
        yield chunk


def import_ports(
    engine: Engine, records: Iterable[PortRecord], *, radius_m: int, prune: bool = False
) -> ImportResult:
    """Upsert ports and rebuild geofences.

    With `prune=True` (full-registry imports only), ports of this source that are absent
    from `records` are deleted when nothing references them; referenced ones are reported.
    """
    started = utcnow()
    seen: set[str] = set()
    with engine.begin() as conn:
        source_id, _ = source_info_sync(conn, SOURCE_KEY)
        total = 0
        for chunk in _chunks(records, BATCH):
            rows = []
            for r in chunk:
                if r.unlocode in seen:  # duplicate rows in the release: first one wins
                    continue
                seen.add(r.unlocode)
                rows.append(
                    {
                        "unlocode": r.unlocode,
                        "name": r.name,
                        "country_code": r.country_code,
                        "lon": r.longitude,
                        "lat": r.latitude,
                        "functions": r.functions,
                    }
                )
            if rows:
                conn.execute(_UPSERT, {"rows": json.dumps(rows), "source_id": source_id})
                total += len(rows)
        removed = 0
        stale: list[str] = []
        if prune and seen:
            params = {"source_id": source_id, "codes": sorted(seen)}
            conn.execute(
                sa.text(
                    "DELETE FROM port_areas WHERE port_id IN (SELECT id FROM ports "
                    "WHERE source_id = :source_id AND NOT (unlocode = ANY(:codes)))"
                ),
                params,
            )
            removed = conn.execute(_DELETE_STALE, params).rowcount
            stale = [r.unlocode for r in conn.execute(_STALE_REFERENCED, params)]
            if stale:
                log.warning("port_registry_stale_referenced", ports=stale)
        conn.execute(_DELETE_AREAS, {"source_id": source_id})
        areas = conn.execute(_INSERT_AREAS, {"source_id": source_id, "radius": radius_m}).rowcount
        write_log_sync(
            conn,
            source_id,
            started,
            status="succeeded",
            received=total,
            accepted=total,
            details={
                "geofence_radius_m": radius_m,
                "areas": areas,
                "removed_stale_ports": removed,
                "stale_ports_still_referenced": stale,
            },
        )
        mark_success_sync(conn, source_id)
    log.info("port_registry_imported", ports=total, areas=areas, radius_m=radius_m)
    return ImportResult(ports=total, areas=areas, removed=removed, stale_referenced=tuple(stale))


def download(url: str, dest: Path) -> Path:
    with httpx.stream("GET", url, timeout=60.0, follow_redirects=True) as resp:
        resp.raise_for_status()
        with dest.open("wb") as fh:
            for chunk in resp.iter_bytes():
                fh.write(chunk)
    return dest


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--file", type=Path, help="local UN/LOCODE code-list CSV")
    parser.add_argument("--url", default=settings.un_locode_url)
    parser.add_argument("--radius-m", type=int, default=settings.port_geofence_radius_m)
    parser.add_argument(
        "--prune",
        action="store_true",
        help="remove ports no longer in the registry (only with a complete registry file)",
    )
    parser.add_argument(
        "--if-empty",
        action="store_true",
        help="skip if the registry is already imported; never fail (used at stack startup)",
    )
    args = parser.parse_args(argv)

    engine = get_sync_engine()
    if args.if_empty:
        with engine.connect() as conn:
            source_id, _ = source_info_sync(conn, SOURCE_KEY)
            existing = conn.execute(
                sa.text("SELECT count(*) FROM ports WHERE source_id = :s"), {"s": source_id}
            ).scalar_one()
        if existing:
            print(f"Port registry already imported ({existing} ports); skipping")
            return 0

    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = args.file or download(args.url, Path(tmp) / "code-list.csv")
            with path.open(encoding="utf-8", newline="") as fh:
                result = import_ports(
                    engine, iter_seaports(fh), radius_m=args.radius_m, prune=args.prune
                )
    except (httpx.HTTPError, OSError) as exc:
        if not args.if_empty:
            raise
        # Startup must not depend on a third-party download; port calls simply stay off.
        log.warning("port_registry_import_skipped", error=f"{type(exc).__name__}: {exc}")
        return 0
    print(
        f"Imported {result.ports} ports, {result.areas} geofences; removed {result.removed} stale"
        + (
            f"; still referenced: {', '.join(result.stale_referenced)}"
            if result.stale_referenced
            else ""
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
