"""ML orchestration: training CLI and scheduled jobs (glue between data sources and ml/).

uv run python -m services.ml_jobs train-eta                 # needs enough real AIS arrivals
uv run python -m services.ml_jobs train-eta --synthetic     # dev only; flagged, never promoted
uv run python -m services.ml_jobs train-delay               # needs observed shipment arrivals
uv run python -m services.ml_jobs train-delay --synthetic [--promote-synthetic]
uv run python -m services.ml_jobs predict | anomalies | congestion | evaluate
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Engine

from core.logging_config import configure_logging, get_logger
from core.settings import get_settings
from core.timeutils import utcnow
from database.session import get_sync_engine
from integrations.carriers.demo import build_events, journey_for
from ml.anomaly_detection.runner import run_anomaly_detection
from ml.congestion_forecasting import pipeline as congestion
from ml.delay_prediction import pipeline as delay
from ml.delay_prediction.features import EventRecord
from ml.eta_prediction import pipeline as eta

log = get_logger(__name__)
Journey = tuple[str, list[EventRecord], str | None, str, datetime, float]


def synthetic_delay_journeys(n: int, now: datetime) -> list[Journey]:
    """Demo-carrier journeys replayed as if polled every 12 h, so each event's availability
    time is when it would first have been seen (point-in-time realistic). SYNTHETIC."""
    out: list[Journey] = []
    for i in range(n):
        ref = f"DEMOTRAIN{i:06d}"
        # Shift each journey back so that it has completed by `now`.
        j = journey_for(ref, now - timedelta(days=60 + (i % 300)))
        seen: dict[str, EventRecord] = {}
        t = j.booked_at
        end = j.booked_at + timedelta(days=j.total_days + 8)
        while t <= end:
            for ev in build_events(j, t):
                if ev["eventID"] not in seen:
                    code = str(ev.get("equipmentEventTypeCode") or ev.get("transportEventTypeCode"))
                    seen[ev["eventID"]] = EventRecord(
                        event_code=code,
                        classifier={"PLN": "PLANNED", "EST": "ESTIMATED", "ACT": "ACTUAL"}[
                            ev["eventClassifierCode"]
                        ],
                        event_at=datetime.fromisoformat(ev["eventDateTime"]),
                        available_at=t,
                        location=ev["eventLocation"]["UNLocationCode"],
                        vessel_name=(ev.get("transportCall") or {})
                        .get("vessel", {})
                        .get("vesselName"),
                    )
            t += timedelta(hours=12)
        events = list(seen.values())
        arrivals = [
            e
            for e in events
            if e.event_code == "ARRI" and e.classifier == "ACTUAL" and e.location == j.destination
        ]
        if not arrivals:
            continue
        actual = arrivals[-1].event_at
        out.append((ref, events, j.origin, j.destination, actual, float(j.delay_h)))
    return out


def real_delay_journeys(
    engine: Engine,
) -> list[Journey]:
    """Shipments with an observed arrival, from non-synthetic sources only."""
    with engine.connect() as conn:
        ships = conn.execute(
            sa.text(
                "SELECT s.id, s.origin_unlocode, s.destination_unlocode, s.actual_arrival_at, s.delay_hours "
                "FROM shipments s JOIN data_sources ds ON ds.id = s.source_id "
                "WHERE ds.data_mode <> 'synthetic' "
                "AND s.actual_arrival_at IS NOT NULL AND s.delay_hours IS NOT NULL "
                "AND s.destination_unlocode IS NOT NULL"
            )
        ).all()
        return [
            (
                str(s.id),
                delay.shipment_events(conn, s.id),
                s.origin_unlocode,
                s.destination_unlocode,
                s.actual_arrival_at,
                float(s.delay_hours),
            )
            for s in ships
        ]


def run_predictions(engine: Engine, now: datetime) -> dict[str, int]:
    s = get_settings()
    with engine.begin() as conn:
        return {
            "eta": eta.predict_live(conn, now),
            "delay": delay.predict_live(
                conn, now, threshold_hours=s.shipment_delay_threshold_hours
            ),
        }


def run_evaluation(engine: Engine, now: datetime) -> dict[str, int]:
    with engine.begin() as conn:
        out = {
            "eta_resolved": eta.backfill_outcomes(conn, now),
            "delay_resolved": delay.backfill_outcomes(conn, now),
            "congestion_resolved": congestion.backfill_and_evaluate(conn, now),
        }
        out["eta_models_evaluated"] = eta.evaluate_live(conn, now)
        out["delay_models_evaluated"] = delay.evaluate_live(conn, now)
    return out


def run_congestion(engine: Engine, now: datetime) -> dict[str, int]:
    with engine.begin() as conn:
        observed = congestion.build_observations(conn, now)
        predicted = congestion.forecast(conn, now)
    return {"observations": observed, "predictions": predicted}


def main(argv: list[str] | None = None) -> int:
    s = get_settings()
    configure_logging(s.log_level, s.log_format)
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_eta = sub.add_parser("train-eta")
    p_eta.add_argument(
        "--synthetic", action="store_true", help="train on generated voyages (flagged)"
    )
    p_delay = sub.add_parser("train-delay")
    p_delay.add_argument(
        "--synthetic", action="store_true", help="train on demo-carrier journeys (flagged)"
    )
    p_delay.add_argument(
        "--promote-synthetic",
        action="store_true",
        help="serve the synthetic-trained model (predictions are labeled)",
    )
    p_delay.add_argument("--n", type=int, default=800)
    for name in ("predict", "anomalies", "congestion", "evaluate"):
        sub.add_parser(name)
    args = parser.parse_args(argv)
    engine, now = get_sync_engine(), utcnow()

    result: Any
    try:
        if args.cmd == "train-eta":
            frame = eta.synthetic_frame() if args.synthetic else None
            result = eta.train(
                engine,
                min_voyages=1 if args.synthetic else s.eta_min_training_voyages,
                frame=frame,
                synthetic=args.synthetic,
            ).__dict__
        elif args.cmd == "train-delay":
            journeys = (
                synthetic_delay_journeys(args.n, now)
                if args.synthetic
                else real_delay_journeys(engine)
            )
            frame = delay.frame_from_journeys(journeys, s.shipment_delay_threshold_hours)
            result = delay.train(
                engine,
                frame,
                synthetic=args.synthetic,
                min_shipments=50 if args.synthetic else s.delay_min_training_shipments,
                threshold_hours=s.shipment_delay_threshold_hours,
                promote_synthetic=args.promote_synthetic,
            ).__dict__
        elif args.cmd == "predict":
            result = run_predictions(engine, now)
        elif args.cmd == "anomalies":
            with engine.begin() as conn:
                result = run_anomaly_detection(conn, now).__dict__
        elif args.cmd == "congestion":
            result = run_congestion(engine, now)
        else:
            result = run_evaluation(engine, now)
    except (eta.InsufficientData, delay.InsufficientData) as exc:
        print(f"Not trained: {exc}")
        return 3
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
