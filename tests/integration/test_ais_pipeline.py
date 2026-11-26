from __future__ import annotations

import io
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from integrations.ais.providers import RawAisMessage
from integrations.ports.unlocode import iter_seaports
from services.ingestion import ais_pipeline
from services.ingestion.ais_pipeline import AisPipeline
from services.ingestion.port_registry import import_ports

T0 = datetime(2026, 11, 20, 8, 0, tzinfo=UTC)
# Two test ports 0.5 deg apart (~55 km), each with an 8 km geofence.
UNLOCODE_CSV = """Change,Country,Location,Name,NameWoDiacritics,Subdivision,Status,Function,Date,IATA,Coordinates,Remarks
,ZZ,AAA,Alpha Port,Alpha Port,,AI,1-------,2401,,1000N 01000E,
,ZZ,BBB,Bravo Port,Bravo Port,,AI,1-------,2401,,1000N 01030E,
,ZZ,APT,Zulu Airport,Zulu Airport,,AI,---4----,2401,,1000N 01015E,
X,ZZ,OLD,Removed Port,Removed Port,,AI,1-------,2401,,1000N 01045E,
"""
ALPHA = (10.0, 10.0)
BRAVO = (10.0, 10.5)
AT_SEA = (10.0, 10.25)


def pos(
    mmsi: int, minutes: float, latlon: tuple[float, float], sog: float, nav: int = 0
) -> RawAisMessage:
    t = T0 + timedelta(minutes=minutes)
    return RawAisMessage(
        {
            "MessageType": "PositionReport",
            "MetaData": {
                "MMSI": mmsi,
                "ShipName": f"TEST {mmsi}",
                "time_utc": t.strftime("%Y-%m-%d %H:%M:%S.%f +0000 UTC"),
            },
            "Message": {
                "PositionReport": {
                    "Latitude": latlon[0],
                    "Longitude": latlon[1],
                    "Sog": sog,
                    "Cog": 90,
                    "TrueHeading": 90,
                    "NavigationalStatus": nav,
                    "PositionAccuracy": True,
                }
            },
        },
        t + timedelta(seconds=2),
    )


def static(
    mmsi: int, minutes: float, *, name: str, imo: int = 0, ship_type: int = 70
) -> RawAisMessage:
    t = T0 + timedelta(minutes=minutes)
    return RawAisMessage(
        {
            "MessageType": "ShipStaticData",
            "MetaData": {"MMSI": mmsi, "time_utc": t.strftime("%Y-%m-%d %H:%M:%S +0000 UTC")},
            "Message": {
                "ShipStaticData": {
                    "Name": name,
                    "CallSign": "TST1",
                    "ImoNumber": imo,
                    "Type": ship_type,
                    "Dimension": {"A": 150, "B": 50, "C": 15, "D": 15},
                    "Destination": "BRAVO",
                }
            },
        },
        t + timedelta(seconds=2),
    )


@pytest.fixture(scope="module")
def ports(migrated_db: Engine) -> None:
    result = import_ports(migrated_db, iter_seaports(io.StringIO(UNLOCODE_CSV)), radius_m=8000)
    assert (result.ports, result.areas) == (2, 2)


@pytest.fixture
async def sessionmaker(
    database_url: str, ports: None
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(database_url)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def pipeline(sessionmaker: async_sessionmaker[AsyncSession]) -> AsyncIterator[AisPipeline]:
    published: list[dict[str, Any]] = []

    async def publish(payload: str) -> None:
        published.append(json.loads(payload))

    p = AisPipeline(
        sessionmaker,
        source_key="ais-simulator",
        receiver_type="synthetic",
        publish=publish,
        log_window_s=3600,
        clock=lambda: T0 + timedelta(hours=6),
    )
    p.published = published  # type: ignore[attr-defined]
    await p.start()
    yield p


def scalar(engine: Engine, sql: str, **params: Any) -> Any:
    with engine.connect() as conn:
        return conn.execute(sa.text(sql), params).scalar()


def rows(engine: Engine, sql: str, **params: Any) -> list[Any]:
    with engine.connect() as conn:
        return list(conn.execute(sa.text(sql), params))


def test_port_registry_skips_non_ports_and_removed_entries(
    migrated_db: Engine, ports: None
) -> None:
    codes = {
        r.unlocode
        for r in rows(migrated_db, "SELECT unlocode FROM ports WHERE country_code = 'ZZ'")
    }
    assert codes == {"ZZAAA", "ZZBBB"}
    inside = scalar(
        migrated_db,
        "SELECT count(*) FROM port_areas a JOIN ports p ON p.id = a.port_id WHERE p.unlocode = 'ZZAAA' "
        "AND ST_Intersects(a.area, ST_MakePoint(10.05, 10.0)::geography)",
    )
    assert inside == 1  # ~5.5 km from the centre: inside an 8 km geofence


def test_port_registry_import_is_idempotent(migrated_db: Engine, ports: None) -> None:
    import_ports(migrated_db, iter_seaports(io.StringIO(UNLOCODE_CSV)), radius_m=6000)
    assert scalar(migrated_db, "SELECT count(*) FROM ports WHERE country_code = 'ZZ'") == 2
    radii = {
        r.radius_m
        for r in rows(
            migrated_db,
            "SELECT radius_m FROM port_areas a JOIN ports p ON p.id = a.port_id WHERE p.country_code = 'ZZ'",
        )
    }
    assert radii == {6000}
    import_ports(migrated_db, iter_seaports(io.StringIO(UNLOCODE_CSV)), radius_m=8000)


async def test_voyage_lifecycle_from_geofences(pipeline: AisPipeline, migrated_db: Engine) -> None:
    mmsi = 211000001
    # First seen already moored at Alpha, then sails to Bravo and stops there.
    for m in [pos(mmsi, 0, ALPHA, 0.0, nav=5), pos(mmsi, 1, ALPHA, 0.0, nav=5)]:
        pipeline.ingest(m)
    await pipeline.flush()
    for m in [
        pos(mmsi, 60, AT_SEA, 14.0),
        pos(mmsi, 120, (10.0, 10.48), 6.0),
        pos(mmsi, 125, BRAVO, 0.2),
    ]:
        pipeline.ingest(m)
    result = await pipeline.flush()
    assert result.inserted == 3 and result.port_events == 2

    vessel_id = scalar(migrated_db, "SELECT id FROM vessels WHERE mmsi = :m", m=mmsi)
    calls = rows(
        migrated_db,
        "SELECT p.unlocode, c.actual_arrival_at, c.actual_departure_at, c.voyage_id FROM voyage_port_calls c "
        "JOIN ports p ON p.id = c.port_id WHERE c.vessel_id = :v ORDER BY c.created_at",
        v=vessel_id,
    )
    assert [c.unlocode for c in calls] == ["ZZAAA", "ZZBBB"]
    # It was already in port when first observed: the arrival time is unknown, not invented.
    assert calls[0].actual_arrival_at is None
    assert calls[0].actual_departure_at == T0 + timedelta(minutes=60)
    assert calls[1].actual_arrival_at == T0 + timedelta(minutes=125)
    assert calls[1].actual_departure_at is None

    voyage = rows(
        migrated_db,
        "SELECT o.unlocode AS origin, d.unlocode AS destination, v.status, v.departed_at, v.arrived_at "
        "FROM voyages v JOIN ports o ON o.id = v.origin_port_id JOIN ports d ON d.id = v.destination_port_id "
        "WHERE v.vessel_id = :v",
        v=vessel_id,
    )
    assert [(v.origin, v.destination, v.status) for v in voyage] == [
        ("ZZAAA", "ZZBBB", "completed")
    ]
    assert calls[1].voyage_id is not None

    latest = rows(
        migrated_db,
        "SELECT last_seen_at, ST_X(last_location::geometry) AS lon, last_nav_status FROM vessels WHERE id = :v",
        v=vessel_id,
    )[0]
    assert latest.last_seen_at == T0 + timedelta(minutes=125)
    assert latest.lon == pytest.approx(10.5)


async def test_duplicates_rejections_and_window_log(
    pipeline: AisPipeline, migrated_db: Engine
) -> None:
    mmsi = 211000002
    msg = pos(mmsi, 300, AT_SEA, 12.0)
    pipeline.ingest(msg)
    pipeline.ingest(msg)  # in-batch duplicate
    bad = pos(mmsi, 301, (91.0, 181.0), 12.0)
    pipeline.ingest(bad)
    pipeline.ingest(RawAisMessage({"MessageType": "BaseStationReport"}, T0))
    await pipeline.flush()
    pipeline.ingest(msg)  # duplicate of an already-stored fix
    await pipeline.flush()

    assert (
        scalar(
            migrated_db,
            "SELECT count(*) FROM vessel_positions p JOIN vessels v ON v.id = p.vessel_id WHERE v.mmsi = :m",
            m=mmsi,
        )
        == 1
    )
    w = pipeline.window
    assert (w.received, w.accepted, w.duplicates, w.ignored) == (5, 1, 2, 1)
    assert dict(w.rejected) == {"invalid_position": 1}

    log_id = await pipeline.close_window()
    log = rows(
        migrated_db,
        "SELECT status, records_received, records_accepted, records_rejected, records_duplicate, details FROM ingestion_logs WHERE id = :id",
        id=log_id,
    )[0]
    assert (
        log.status,
        log.records_received,
        log.records_accepted,
        log.records_rejected,
        log.records_duplicate,
    ) == ("partial", 5, 1, 1, 2)
    assert log.details["rejected_by_reason"] == {"invalid_position": 1}
    source = rows(
        migrated_db, "SELECT status, last_success_at FROM data_sources WHERE key = 'ais-simulator'"
    )[0]
    assert source.status == "active" and source.last_success_at is not None


async def test_live_updates_carry_data_mode(pipeline: AisPipeline) -> None:
    pipeline.ingest(pos(211000003, 400, AT_SEA, 10.0))
    await pipeline.flush()
    published = pipeline.published  # type: ignore[attr-defined]
    assert published[-1]["source"] == "ais-simulator"
    assert published[-1]["data_mode"] == "synthetic"
    assert published[-1]["positions"][0]["mmsi"] == 211000003


async def test_static_data_classifies_and_infers_carrier(
    pipeline: AisPipeline, migrated_db: Engine
) -> None:
    mmsi = 211000004
    pipeline.ingest(static(mmsi, 500, name="MAERSK TESTER", ship_type=71))
    pipeline.ingest(pos(mmsi, 500, AT_SEA, 15.0))
    await pipeline.flush()
    v = rows(
        migrated_db,
        "SELECT id, name, vessel_class, vessel_class_source, length_m, destination FROM vessels WHERE mmsi = :m",
        m=mmsi,
    )[0]
    assert (v.name, v.vessel_class, v.vessel_class_source, v.length_m, v.destination) == (
        "MAERSK TESTER", "cargo", "ais_ship_type", 200, "BRAVO",
    )  # fmt: skip
    assoc = rows(
        migrated_db,
        "SELECT c.code, h.method, h.confidence FROM vessel_carrier_history h JOIN carriers c ON c.id = h.carrier_id WHERE h.vessel_id = :v",
        v=v.id,
    )
    assert [(a.code, a.method, float(a.confidence)) for a in assoc] == [
        ("maersk", "name_heuristic", 0.6)
    ]


async def test_mmsi_moves_to_vessel_identified_by_imo(
    pipeline: AisPipeline, migrated_db: Engine
) -> None:
    old_mmsi, new_mmsi, imo = 211000005, 211000006, 9176187
    pipeline.ingest(static(old_mmsi, 600, name="IDENTITY TEST", imo=imo))
    pipeline.ingest(pos(old_mmsi, 600, AT_SEA, 12.0))
    await pipeline.flush()
    original = scalar(migrated_db, "SELECT id FROM vessels WHERE imo = :imo", imo=imo)

    # Reflagged: same ship (same IMO) now transmits a new MMSI.
    pipeline.ingest(pos(new_mmsi, 700, AT_SEA, 12.0))
    await pipeline.flush()
    pipeline.ingest(static(new_mmsi, 701, name="IDENTITY TEST", imo=imo))
    pipeline.ingest(pos(new_mmsi, 702, AT_SEA, 12.0))
    await pipeline.flush()

    active = rows(
        migrated_db,
        "SELECT vessel_id, value FROM vessel_identifiers WHERE scheme = 'mmsi' AND valid_to IS NULL AND value IN (:a, :b)",
        a=str(old_mmsi),
        b=str(new_mmsi),
    )
    assert [(r.vessel_id, r.value) for r in active] == [(original, str(new_mmsi))]
    closed = scalar(
        migrated_db,
        "SELECT count(*) FROM vessel_identifiers WHERE scheme = 'mmsi' AND valid_to IS NOT NULL AND value IN (:a, :b)",
        a=str(old_mmsi),
        b=str(new_mmsi),
    )
    assert closed == 2  # history kept, not deleted
    assert scalar(migrated_db, "SELECT mmsi FROM vessels WHERE id = :v", v=original) == new_mmsi
    assert (
        scalar(
            migrated_db,
            "SELECT count(*) FROM vessel_positions WHERE vessel_id = :v AND observed_at = :t",
            v=original,
            t=T0 + timedelta(minutes=702),
        )
        == 1
    )


async def test_failed_flush_is_recorded_as_failure(
    pipeline: AisPipeline, migrated_db: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(*_: Any, **__: Any) -> None:
        raise RuntimeError("simulated database failure")

    monkeypatch.setattr(ais_pipeline, "process_port_calls", boom)
    pipeline.ingest(pos(211000007, 800, AT_SEA, 12.0))
    with pytest.raises(RuntimeError):
        await pipeline.flush()
    assert (
        scalar(migrated_db, "SELECT count(*) FROM vessels WHERE mmsi = 211000007") == 0
    )  # rolled back

    log_id = await pipeline.close_window()
    log = rows(
        migrated_db, "SELECT status, error_message FROM ingestion_logs WHERE id = :id", id=log_id
    )[0]
    assert log.status == "failed"
    assert "simulated database failure" in log.error_message
    assert (
        scalar(migrated_db, "SELECT status FROM data_sources WHERE key = 'ais-simulator'")
        == "error"
    )
