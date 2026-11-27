"""Research experiments: a stated hypothesis, a fixed configuration, and reproducible runs.

Each run records the code version, a hash of the exact input data, every parameter
configuration that was searched, the per-fold choices, the out-of-sample positions it took,
and in-sample vs out-of-sample metrics next to a buy-and-hold benchmark.
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Connection, Engine

from core.logging_config import get_logger
from core.timeutils import utcnow
from ml.quant_research import stats
from ml.quant_research.backtest import BacktestConfig, run_backtest
from ml.quant_research.features import FeatureSpec, compute_feature, market_prices
from ml.quant_research.pit import snapshot_hash

log = get_logger(__name__)

_FAIL_RUN = sa.text(
    "UPDATE backtest_runs SET status = 'failed', error = :err, finished_at = now() WHERE id = :r"
)
_SET_STATUS = sa.text(
    "UPDATE research_experiments SET status = :status, updated_at = now() WHERE id = :e"
)
_INSERT_METRICS = sa.text(
    """
    INSERT INTO backtest_metrics (backtest_run_id, split, metric, value, benchmark_value)
    SELECT :r, m.split, m.metric, m.value, m.benchmark
    FROM jsonb_to_recordset(CAST(:rows AS jsonb))
        AS m(split text, metric text, value double precision, benchmark double precision)
    """
)
_INSERT_SIGNALS = sa.text(
    """
    INSERT INTO research_signals (experiment_id, backtest_run_id, signal_key, as_of, available_at,
        value, position)
    SELECT :e, :r, :k, s.t, s.avail, s.value, s.pos
    FROM jsonb_to_recordset(CAST(:rows AS jsonb))
        AS s(t timestamptz, avail timestamptz, value double precision, pos double precision)
    """
)


@dataclass(frozen=True)
class ExperimentConfig:
    feature: str  # base|frequency|transform
    asset: str  # market series key, e.g. fred:DCOILBRENTEU
    execution_lag_days: int = 0
    backtest: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> tuple[FeatureSpec, BacktestConfig]:
        spec = FeatureSpec.parse(self.feature)
        bt = dict(self.backtest)
        for k in ("lookbacks", "thresholds", "directions"):
            if k in bt:
                bt[k] = tuple(bt[k])
        cfg = BacktestConfig(
            frequency=spec.frequency, **{k: v for k, v in bt.items() if k != "frequency"}
        )
        if not 0 <= self.execution_lag_days <= 10:
            raise ValueError("execution_lag_days must be between 0 and 10")
        if not self.asset.startswith("fred:"):
            raise ValueError("asset must be a market series key such as fred:DCOILBRENTEU")
        return spec, cfg

    def as_dict(self) -> dict[str, Any]:
        return {"feature": self.feature, "asset": self.asset,
                "execution_lag_days": self.execution_lag_days, "backtest": self.backtest}  # fmt: skip


def code_version() -> str | None:
    """Git commit of the running code: CHAINSIGHT_GIT_SHA (set in images) or `git rev-parse`."""
    sha = os.environ.get("CHAINSIGHT_GIT_SHA")
    if sha:
        return sha[:40]
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],  # noqa: S607
            capture_output=True, text=True, timeout=5, cwd=Path(__file__).resolve().parent, check=False,
        )  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip()[:40] or None


def create_experiment(
    conn: Connection,
    organization_id: uuid.UUID,
    *,
    name: str,
    hypothesis: str,
    config: ExperimentConfig,
) -> uuid.UUID:
    config.validate()
    eid: uuid.UUID = conn.execute(
        sa.text(
            "INSERT INTO research_experiments (organization_id, name, hypothesis, config) "
            "VALUES (:o, :n, :h, CAST(:c AS jsonb)) RETURNING id"
        ),
        {"o": organization_id, "n": name, "h": hypothesis, "c": json.dumps(config.as_dict())},
    ).scalar_one()
    return eid


def _signals(frame: Any, series: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The signal value, its availability and the position held, per out-of-sample period."""
    out = []
    for point in series:
        t = datetime.fromisoformat(point["t"])
        row = frame.loc[t]
        avail = row["x_available_at"]
        x = row["x"]
        out.append({
            "t": point["t"],
            "avail": avail.isoformat() if hasattr(avail, "isoformat") and avail == avail else point["t"],
            "value": float(x) if x == x else None,
            "pos": point["position"],
        })  # fmt: skip
    return out


def run_experiment(
    engine: Engine, experiment_id: uuid.UUID, now: datetime | None = None
) -> uuid.UUID:
    """Run (or re-run) an experiment's backtest. Failures are recorded on the run, not hidden."""
    now = now or utcnow()
    with engine.begin() as conn:
        raw_cfg = conn.execute(
            sa.text("SELECT config FROM research_experiments WHERE id = :e"), {"e": experiment_id}
        ).scalar_one()
        cfg = ExperimentConfig(**raw_cfg)
        run_id: uuid.UUID = conn.execute(
            sa.text(
                "INSERT INTO backtest_runs (experiment_id, started_at, config, code_version) "
                "VALUES (:e, :t, CAST(:c AS jsonb), :v) RETURNING id"
            ),
            {"e": experiment_id, "t": now, "c": json.dumps(raw_cfg), "v": code_version()},
        ).scalar_one()
    try:
        spec, bt_cfg = cfg.validate()
        with engine.connect() as conn:
            feature = compute_feature(conn, spec)
            prices = market_prices(conn, cfg.asset)
        frame = stats.align(
            feature, prices, spec.frequency, execution_lag_days=cfg.execution_lag_days
        )
        result = run_backtest(frame, bt_cfg)
    except Exception as exc:
        with engine.begin() as conn:
            conn.execute(_FAIL_RUN, {"r": run_id, "err": f"{type(exc).__name__}: {exc}"[:2000]})
            conn.execute(_SET_STATUS, {"e": experiment_id, "status": "failed"})
        log.warning("backtest_failed", experiment_id=str(experiment_id), error=str(exc))
        raise
    signals = _signals(frame, result.series)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "UPDATE backtest_runs SET status = 'succeeded', finished_at = now(), "
                "data_snapshot_hash = :h, series = CAST(:s AS jsonb), folds = CAST(:f AS jsonb), "
                "config = CAST(:c AS jsonb) WHERE id = :r"
            ),
            {"r": run_id, "h": snapshot_hash(feature, prices), "s": json.dumps({
                "points": result.series, "in_sample_params": result.in_sample_params,
                "n_configs_tested": result.n_configs_tested,
                "trial_sharpes": result.trial_sharpes}),
             "f": json.dumps(result.folds),
             "c": json.dumps(raw_cfg | {"resolved_backtest": result.config})},
        )  # fmt: skip
        conn.execute(
            _INSERT_METRICS,
            {"r": run_id, "rows": json.dumps([
                {"split": split, "metric": k, "value": v["value"], "benchmark": v["benchmark"]}
                for split, ms in result.metrics.items() for k, v in ms.items()])},
        )  # fmt: skip
        if signals:
            conn.execute(
                _INSERT_SIGNALS,
                {"e": experiment_id, "r": run_id, "k": cfg.feature, "rows": json.dumps(signals)},
            )
        conn.execute(_SET_STATUS, {"e": experiment_id, "status": "completed"})
    return run_id
