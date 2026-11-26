"""Quant research jobs: data collection, stored datasets, and experiments.

uv run python -m services.research_jobs collect            # PortWatch + FRED (incremental)
uv run python -m services.research_jobs build-dataset      # stored weekly feature dataset
uv run python -m services.research_jobs seed-experiments   # a priori example hypotheses
uv run python -m services.research_jobs run <experiment-id>|all
"""

from __future__ import annotations

import argparse
import json
import uuid
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Engine

from core.logging_config import configure_logging, get_logger
from core.settings import get_settings
from database.session import get_sync_engine
from integrations.research.http import ResearchSourceError
from ml.quant_research.datasets import build_dataset
from ml.quant_research.experiments import ExperimentConfig, create_experiment, run_experiment
from services.ingestion.research_data import collect_fred, collect_portwatch

log = get_logger(__name__)
LOCAL_ORG_SLUG = "local"


@dataclass(frozen=True)
class SeedExperiment:
    name: str
    hypothesis: str
    config: ExperimentConfig


# Written down before any of them was run. They are examples of the method, not claims:
# most single-feature timing rules on liquid commodities show no out-of-sample edge.
SEED_EXPERIMENTS = (
    SeedExperiment(
        "Gulf tanker transits vs Brent",
        "A year-over-year rise in tanker transits through Hormuz and Bab el-Mandeb signals more "
        "Middle East supply reaching the market, which should precede weaker Brent returns.",
        ExperimentConfig(feature="composite:hormuz_bab_tanker|W|yoy", asset="fred:DCOILBRENTEU"),
    ),
    SeedExperiment(
        "Red Sea rerouting vs Brent",
        "When tankers divert from Suez to the Cape of Good Hope (Suez share falls), voyages "
        "lengthen and effective supply tightens, which should precede stronger Brent returns.",
        ExperimentConfig(feature="composite:suez_share_tanker|W|z26", asset="fred:DCOILBRENTEU"),
    ),
    SeedExperiment(
        "Malacca tanker capacity vs WTI",
        "Rising tanker capacity through Malacca reflects stronger East Asian crude demand, which "
        "should precede stronger WTI returns.",
        ExperimentConfig(
            feature="composite:malacca_tanker_capacity|W|yoy", asset="fred:DCOILWTICO"
        ),
    ),
)


def local_org(engine: Engine) -> uuid.UUID:
    with engine.connect() as conn:
        org: uuid.UUID = conn.execute(
            sa.text("SELECT id FROM organizations WHERE slug = :s"), {"s": LOCAL_ORG_SLUG}
        ).scalar_one()
    return org


def collect(engine: Engine) -> dict[str, Any]:
    """Collect both sources; one failing does not stop the other."""
    s = get_settings()
    out: dict[str, Any] = {}
    for name, fn in (("fred", collect_fred), ("portwatch", collect_portwatch)):
        try:
            out[name] = fn(engine, s).__dict__
        except ResearchSourceError as exc:
            out[name] = {"error": str(exc)}
    return out


def seed_experiments(engine: Engine) -> list[str]:
    org = local_org(engine)
    created = []
    with engine.begin() as conn:
        existing = set(conn.execute(sa.text("SELECT name FROM research_experiments")).scalars())
        for e in SEED_EXPERIMENTS:
            if e.name not in existing:
                created.append(str(create_experiment(conn, org, name=e.name, hypothesis=e.hypothesis,
                                                     config=e.config)))  # fmt: skip
    return created


def run_all(engine: Engine) -> dict[str, str]:
    with engine.connect() as conn:
        ids = list(
            conn.execute(
                sa.text("SELECT id FROM research_experiments ORDER BY created_at")
            ).scalars()
        )
    out = {}
    for eid in ids:
        try:
            out[str(eid)] = str(run_experiment(engine, eid))
        except Exception as exc:
            out[str(eid)] = f"failed: {exc}"
    return out


def main(argv: list[str] | None = None) -> int:
    s = get_settings()
    configure_logging(s.log_level, s.log_format)
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("collect")
    sub.add_parser("build-dataset")
    sub.add_parser("seed-experiments")
    p_run = sub.add_parser("run")
    p_run.add_argument("experiment", help="experiment id or 'all'")
    args = parser.parse_args(argv)
    engine = get_sync_engine()
    result: Any
    if args.cmd == "collect":
        result = collect(engine)
    elif args.cmd == "build-dataset":
        result = build_dataset(engine)
    elif args.cmd == "seed-experiments":
        result = seed_experiments(engine)
    elif args.experiment == "all":
        result = run_all(engine)
    else:
        result = str(run_experiment(engine, uuid.UUID(args.experiment)))
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
