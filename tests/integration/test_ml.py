from __future__ import annotations

import asyncio
import io
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from core.settings import get_settings
from integrations.carriers.demo import DemoCarrierAdapter
from integrations.carriers.models import TrackingReference
from integrations.ports.unlocode import iter_seaports
from ml.anomaly_detection.runner import run_anomaly_detection
from ml.congestion_forecasting import pipeline as congestion
from ml.delay_prediction import pipeline as delay
from ml.eta_prediction import pipeline as eta
from services.ingestion.port_registry import import_ports
from services.ml_jobs import synthetic_delay_journeys
from services.shipment_tracking.registry import CarrierRegistry
from services.shipment_tracking.service import track

NOW = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
CSV = """Change,Country,Location,Name,NameWoDiacritics,Subdivision,Status,Function,Date,IATA,Coordinates,Remarks
,ZW,MLA,Ml Alpha,Ml Alpha,,AI,1-------,2401,,1000N 12000E,
,ZW,MLB,Ml Bravo,Ml Bravo,,AI,1-------,2401,,1200N 12000E,
"""


def q(engine: Engine, sql: str, **params: Any) -> list[Any]:
    with engine.connect() as conn:
        return list(conn.execute(sa.text(sql), params))


@pytest.fixture(scope="module")
def ml_env(migrated_db: Engine, tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    import os

    old = {k: os.environ.get(k) for k in ("MODEL_ARTIFACT_DIR", "MLFLOW_TRACKING_URI")}
    os.environ["MODEL_ARTIFACT_DIR"] = str(tmp_path_factory.mktemp("models"))
    os.environ["MLFLOW_TRACKING_URI"] = ""  # tracking disabled in tests
    get_settings.cache_clear()
    import_ports(migrated_db, iter_seaports(io.StringIO(CSV)), radius_m=8000)
    yield
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    get_settings.cache_clear()


def make_vessel(
    engine: Engine, mmsi: int, *, destination: str | None = None, watched: bool = True
) -> uuid.UUID:
    with engine.begin() as conn:
        src = conn.execute(
            sa.text("SELECT id FROM data_sources WHERE key = 'aisstream'")
        ).scalar_one()
        vid = conn.execute(
            sa.text("INSERT INTO vessels (mmsi, name, destination, last_position_source_id, vessel_class) "
                    "VALUES (:m, :n, :d, :s, 'cargo') RETURNING id"),
            {"m": mmsi, "n": f"ML TEST {mmsi}", "d": destination, "s": src},
        ).scalar_one()  # fmt: skip
        if watched:
            conn.execute(
                sa.text("INSERT INTO vessel_carrier_history (vessel_id, carrier_id, relation, method, confidence) "
                        "SELECT :v, id, 'operator', 'manual', 1 FROM carriers WHERE code = 'msc'"),
                {"v": vid},
            )  # fmt: skip
    return vid


def add_track(
    engine: Engine, vid: uuid.UUID, points: list[tuple[datetime, float, float, float, int]]
) -> None:
    with engine.begin() as conn:
        src = conn.execute(
            sa.text("SELECT id FROM data_sources WHERE key = 'aisstream'")
        ).scalar_one()
        for t, lat, lon, sog, nav in points:
            conn.execute(
                sa.text("INSERT INTO vessel_positions (vessel_id, observed_at, source_id, location, sog_knots, "
                        "cog_deg, nav_status, message_type) VALUES (:v, :t, :s, ST_MakePoint(:lon, :lat)::geography, "
                        ":sog, 0, :nav, 'PositionReport')"),
                {"v": vid, "t": t, "s": src, "lat": lat, "lon": lon, "sog": sog, "nav": nav},
            )  # fmt: skip
        last = points[-1]
        conn.execute(
            sa.text("UPDATE vessels SET last_seen_at = :t, last_location = ST_MakePoint(:lon, :lat)::geography, "
                    "last_sog_knots = :sog, last_cog_deg = 0, last_nav_status = :nav WHERE id = :v"),
            {"v": vid, "t": last[0], "lat": last[1], "lon": last[2], "sog": last[3], "nav": last[4]},
        )  # fmt: skip


def feed_windows(
    engine: Engine, start: datetime, end: datetime, gap: tuple[datetime, datetime] | None = None
) -> None:
    with engine.begin() as conn:
        src = conn.execute(
            sa.text("SELECT id FROM data_sources WHERE key = 'aisstream'")
        ).scalar_one()
        conn.execute(sa.text("DELETE FROM ingestion_logs WHERE data_source_id = :s"), {"s": src})
        t = start
        while t < end:
            if not (gap and gap[0] <= t < gap[1]):
                conn.execute(
                    sa.text("INSERT INTO ingestion_logs (data_source_id, started_at, finished_at, status, "
                            "records_received, records_accepted) VALUES (:s, :a, :b, 'succeeded', 10, 10)"),
                    {"s": src, "a": t, "b": t + timedelta(minutes=1)},
                )  # fmt: skip
            t += timedelta(minutes=1)


def test_predictions_are_append_only(migrated_db: Engine, ml_env: None) -> None:
    vid = make_vessel(migrated_db, 241000001, watched=False)
    with migrated_db.begin() as conn:
        mv = eta.ensure_rule_version(conn, "eta", eta.BASELINE_VERSION, "rule", "baseline")
        pid = conn.execute(
            sa.text("INSERT INTO eta_predictions (vessel_id, model_version_id, input_data_as_of, predicted_arrival_at) "
                    "VALUES (:v, :m, :t, :t) RETURNING id"),
            {"v": vid, "m": mv, "t": NOW},
        ).scalar_one()  # fmt: skip
    with pytest.raises(DBAPIError, match="append-only"), migrated_db.begin() as conn:
        conn.execute(
            sa.text("UPDATE eta_predictions SET predicted_arrival_at = now() WHERE id = :id"),
            {"id": pid},
        )
    with migrated_db.begin() as conn:  # outcome columns may be filled in
        conn.execute(sa.text("UPDATE eta_predictions SET actual_arrival_at = :t, error_hours = 1.5 WHERE id = :id"),
                     {"id": pid, "t": NOW})  # fmt: skip


def test_anomaly_stop_raised_then_resolved_and_silence_needs_continuous_feed(
    migrated_db: Engine, ml_env: None
) -> None:
    stopper = make_vessel(migrated_db, 241000002)
    add_track(
        migrated_db,
        stopper,
        [(NOW - timedelta(minutes=m), 11.0, 125.0, 0.1, 0) for m in range(80, -1, -10)],
    )
    silent = make_vessel(migrated_db, 241000003)
    add_track(
        migrated_db,
        silent,
        [(NOW - timedelta(hours=3, minutes=m), 11.5, 125.5, 14.0, 0) for m in (20, 10, 0)],
    )

    feed_windows(
        migrated_db,
        NOW - timedelta(hours=4),
        NOW,
        gap=(NOW - timedelta(hours=2), NOW - timedelta(hours=1)),
    )
    with migrated_db.begin() as conn:
        run_anomaly_detection(conn, NOW)
    open_alerts = {
        (r.entity_id, r.details["type"])
        for r in q(
            migrated_db,
            "SELECT entity_id, details FROM system_alerts WHERE category = 'vessel_anomaly' AND resolved_at IS NULL",
        )
    }
    assert (stopper, "unexpected_stop") in open_alerts
    assert (silent, "ais_silence") not in open_alerts  # our feed had an hour-long gap

    feed_windows(migrated_db, NOW - timedelta(hours=4), NOW)  # continuous feed
    with migrated_db.begin() as conn:
        run_anomaly_detection(conn, NOW)
    open_alerts = {
        (r.entity_id, r.details["type"])
        for r in q(
            migrated_db,
            "SELECT entity_id, details FROM system_alerts WHERE category = 'vessel_anomaly' AND resolved_at IS NULL",
        )
    }
    assert (silent, "ais_silence") in open_alerts

    later = NOW + timedelta(minutes=10)
    add_track(migrated_db, stopper, [(later, 11.01, 125.0, 12.0, 0)])  # moving again
    with migrated_db.begin() as conn:
        run_anomaly_detection(conn, later)
    still = q(
        migrated_db,
        "SELECT 1 FROM system_alerts WHERE dedupe_key = :k AND resolved_at IS NULL",
        k=f"anomaly:unexpected_stop:{stopper}",
    )
    assert not still


def test_eta_synthetic_training_is_flagged_and_never_promoted(
    migrated_db: Engine, ml_env: None
) -> None:
    result = eta.train(migrated_db, min_voyages=1, frame=eta.synthetic_frame(150), synthetic=True)
    assert result.promoted is False
    row = q(
        migrated_db,
        "SELECT is_synthetic_trained, status, feature_schema, artifact_uri FROM model_versions WHERE id = :id",
        id=result.model_version_id,
    )[0]
    assert row.is_synthetic_trained and row.status == "candidate"
    assert row.feature_schema["training_domain"]["max_label_hours"] > 0
    assert Path(row.artifact_uri).exists()
    evals = q(
        migrated_db,
        "SELECT split, data_mode, metrics FROM model_evaluations WHERE model_version_id = :id",
        id=result.model_version_id,
    )
    assert [(e.split, e.data_mode) for e in evals] == [("test", "synthetic")]
    assert {
        "mae_hours",
        "median_ae_hours",
        "rmse_hours",
        "within_6h",
        "within_12h",
        "within_24h",
    } <= set(evals[0].metrics)


def test_eta_refuses_too_little_real_data(migrated_db: Engine, ml_env: None) -> None:
    with pytest.raises(eta.InsufficientData, match="AIS-observed arrivals"):
        eta.train(migrated_db, min_voyages=10_000)


def test_eta_live_prediction_and_outcome_backfill(migrated_db: Engine, ml_env: None) -> None:
    vid = make_vessel(migrated_db, 241000004, destination="ZW MLB")
    now = datetime.now(UTC).replace(microsecond=0)
    add_track(
        migrated_db,
        vid,
        [(now - timedelta(minutes=m), 11.0 + (60 - m) / 600, 120.0, 12.0, 0) for m in (60, 30, 0)],
    )
    with migrated_db.begin() as conn:
        assert eta.predict_live(conn, now) >= 1
        assert eta.predict_live(conn, now) == 0  # rate-limited: one per vessel per hour
    pred = q(migrated_db, "SELECT * FROM eta_predictions WHERE vessel_id = :v", v=vid)[0]
    assert pred.target_unlocode == "ZWMLB" and pred.features["destination_resolution"] == "unlocode"
    assert (
        pred.input_data_as_of == now
        and pred.lower_bound_at < pred.predicted_arrival_at < pred.upper_bound_at
    )
    assert 50 < pred.remaining_distance_nm < 70  # ~1 deg of latitude

    arrival = now + timedelta(hours=6)
    with migrated_db.begin() as conn:
        port = conn.execute(sa.text("SELECT id FROM ports WHERE unlocode = 'ZWMLB'")).scalar_one()
        conn.execute(sa.text("INSERT INTO voyage_port_calls (vessel_id, port_id, actual_arrival_at, detection_method) "
                             "VALUES (:v, :p, :a, 'ais_geofence')"), {"v": vid, "p": port, "a": arrival})  # fmt: skip
        assert eta.backfill_outcomes(conn, arrival) >= 1
        assert eta.evaluate_live(conn, arrival) >= 1
    resolved = q(
        migrated_db,
        "SELECT actual_arrival_at, error_hours FROM eta_predictions WHERE vessel_id = :v",
        v=vid,
    )[0]
    assert resolved.actual_arrival_at == arrival
    expected = (pred.predicted_arrival_at - arrival).total_seconds() / 3600
    assert resolved.error_hours == pytest.approx(expected, abs=0.01)


def test_synthetic_delay_model_only_scores_synthetic_shipments(
    database_url: str, migrated_db: Engine, ml_env: None
) -> None:
    frame = delay.frame_from_journeys(synthetic_delay_journeys(160, NOW), threshold_hours=12)
    assert set(frame["label"]) == {0, 1}
    result = delay.train(
        migrated_db,
        frame,
        synthetic=True,
        min_shipments=50,
        threshold_hours=12,
        promote_synthetic=True,
    )
    assert result.promoted and {
        "pr_auc",
        "roc_auc",
        "precision",
        "recall",
        "f1",
        "brier",
        "ece",
    } <= set(result.test_metrics)
    assert result.test_metrics["calibration"]

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    async def make_shipments() -> None:
        engine = create_async_engine(database_url)
        sm = async_sessionmaker(engine, expire_on_commit=False)
        registry = CarrierRegistry(adapters={"demo": DemoCarrierAdapter()})
        async with sm() as session, session.begin():
            for i in range(30):
                await track(session, registry, carrier_code="demo",
                            reference=TrackingReference(reference_type="booking", value=f"DEMOML{i:04d}"),
                            threshold_hours=12)  # fmt: skip
        await engine.dispose()

    asyncio.run(make_shipments())
    with migrated_db.begin() as conn:
        n = delay.predict_live(conn, datetime.now(UTC), threshold_hours=12)
    assert n >= 1
    scored = q(
        migrated_db,
        "SELECT DISTINCT ds.data_mode FROM delay_predictions d JOIN shipments s ON s.id = d.shipment_id JOIN data_sources ds ON ds.id = s.source_id",
    )
    assert [r.data_mode for r in scored] == ["synthetic"]  # real/historical shipments never scored
    risk = q(
        migrated_db, "SELECT level, factors FROM risk_assessments ORDER BY assessed_at DESC LIMIT 1"
    )[0]
    assert risk.level in {"low", "medium", "high"} and isinstance(risk.factors, list)


def test_congestion_observations_and_forecast(migrated_db: Engine, ml_env: None) -> None:
    vid = make_vessel(migrated_db, 241000005, watched=False)
    now = datetime.now(UTC)
    with migrated_db.begin() as conn:
        port = conn.execute(sa.text("SELECT id FROM ports WHERE unlocode = 'ZWMLA'")).scalar_one()
        conn.execute(sa.text("INSERT INTO voyage_port_calls (vessel_id, port_id, actual_arrival_at, detection_method, created_at) "
                             "VALUES (:v, :p, :a, 'ais_geofence', :a)"), {"v": vid, "p": port, "a": now - timedelta(hours=30)})  # fmt: skip
        assert congestion.build_observations(conn, now) > 0
        assert congestion.forecast(conn, now) > 0
    obs = q(
        migrated_db,
        "SELECT value FROM port_observations o JOIN ports p ON p.id = o.port_id WHERE p.unlocode = 'ZWMLA' AND metric = 'vessels_in_port' ORDER BY observed_at DESC LIMIT 1",
    )
    assert obs[0].value == 1
    preds = q(
        migrated_db,
        "SELECT m.version, c.lower_bound FROM congestion_predictions c JOIN model_versions m ON m.id = c.model_version_id JOIN ports p ON p.id = c.port_id WHERE p.unlocode = 'ZWMLA'",
    )
    assert len(preds) == 24 and {p.version for p in preds} <= {
        "seasonal-naive-24h-v1",
        "persistence-v1",
    }
    assert all(p.lower_bound is None for p in preds)  # no error history yet: no invented interval


def test_prediction_api_exposes_provenance_and_synthetic_flags(
    migrated_db: Engine, ml_env: None
) -> None:
    """Runs after the tests above (module order), so the DB holds ETA, delay, congestion
    predictions and anomaly alerts. Every row must carry model version and timing."""
    from fastapi.testclient import TestClient

    from chainsight_api.main import create_app

    with TestClient(create_app()) as c:
        models = c.get("/api/v1/models").json()
        assert {m["model_name"] for m in models} >= {"eta", "delay", "congestion"}
        assert all("is_synthetic_trained" in m and "evaluations" in m for m in models)
        delay_models = [
            m for m in models if m["model_name"] == "delay" and m["status"] == "production"
        ]
        assert delay_models and all(m["is_synthetic_trained"] for m in delay_models)

        summary = c.get("/api/v1/predictions/summary").json()
        assert summary["delay_model_synthetic"] is True
        assert summary["congestion_ports"] >= 1

        etas = c.get("/api/v1/predictions/eta").json()
        for p in etas:
            assert p["model_version"] and p["predicted_at"] and p["input_data_as_of"]
        # Pipeline-produced rows say how their interval was built (hand-inserted ones may not).
        assert any(p["interval_method"] for p in etas)

        delays = c.get("/api/v1/predictions/delay").json()
        assert delays and all(d["is_synthetic_trained"] for d in delays)
        assert all(d["shipment_data_mode"] == "synthetic" for d in delays)
        assert all(0 <= d["delay_probability"] <= 1 for d in delays)
        history = c.get(f"/api/v1/shipments/{delays[0]['shipment_id']}/delay-risk").json()
        assert history and history[0]["id"] == delays[0]["id"]

        cong = c.get("/api/v1/predictions/congestion").json()
        zw = next(p for p in cong if p["unlocode"] == "ZWMLA")
        assert len(zw["forecast"]) == 24 and zw["method"]
        port = c.get(f"/api/v1/ports/{zw['port_id']}/congestion").json()
        assert port["observations"]["vessels_in_port"] and len(port["forecast"]) == 24

        anomalies = c.get("/api/v1/anomalies", params={"status": "all"}).json()
        assert all(a["type"] and a["raised_at"] for a in anomalies)
        assert c.get("/api/v1/anomalies", params={"status": "bogus"}).status_code == 422
