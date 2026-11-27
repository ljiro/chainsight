"""Research read models and analyses for the API (synchronous; the API runs them in a
worker thread). Every response states how the data was aligned in time."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import numpy as np
import pandas as pd
import sqlalchemy as sa
from sqlalchemy.engine import Connection, Engine

from ml.quant_research import multiple_testing as mt
from ml.quant_research import stats
from ml.quant_research.experiments import ExperimentConfig, create_experiment, run_experiment
from ml.quant_research.features import (
    AIS_METRICS,
    COMPOSITES,
    FeatureError,
    FeatureSpec,
    catalog,
    compute_feature,
    market_prices,
)
from ml.quant_research.pit import known_at

ALIGNMENT_NOTE = (
    "x is the feature value known at each decision time (00:00 UTC the day after the period "
    "ends, using each value's availability time). y is the asset's log return from the close of "
    "the first trading day on or after the decision to the next decision's entry close."
)
DISCLAIMER = (
    "Research output, not investment advice. Many features, lags and parameters are tested "
    "here, so some results will look significant by chance."
)


class NotFound(LookupError):
    pass


def _points(df: pd.DataFrame) -> list[dict[str, Any]]:
    return [
        {"as_of": r.as_of.isoformat(), "value": None if np.isnan(r.value) else round(float(r.value), 6),
         "available_at": r.available_at.isoformat()}
        for r in df.itertuples(index=False)
    ]  # fmt: skip


def indicators(conn: Connection) -> dict[str, Any]:
    return {
        "indicators": catalog(conn),
        "ais_metrics": AIS_METRICS,
        "transforms": ["level", "yoy", "ma4", "ma13", "pct1", "pct4", "z26", "z52", "diff1"],
        "frequencies": {"D": "daily", "W": "weekly (Fri)", "M": "monthly"},
    }


def feature_series(conn: Connection, key: str) -> dict[str, Any]:
    spec = FeatureSpec.parse(key)
    df = compute_feature(conn, spec)
    if df.empty:
        raise FeatureError(f"no data for {key!r}")
    lag_days = (df["available_at"] - df["as_of"]).dt.total_seconds() / 86400
    name = COMPOSITES[spec.base].name if spec.base in COMPOSITES else spec.base
    return {"key": spec.key, "name": name, "frequency": spec.frequency, "transform": spec.transform,
            "n": len(df), "median_availability_lag_days": round(float(lag_days.median()), 1),
            "points": _points(df)}  # fmt: skip


def market_series(conn: Connection, key: str) -> dict[str, Any]:
    df = market_prices(conn, key)
    meta = conn.execute(
        sa.text("SELECT name, unit, attributes FROM indicator_series WHERE key = :k"), {"k": key}
    ).one()
    return {"key": key, "name": meta.name, "unit": meta.unit, "attributes": meta.attributes,
            "n": len(df), "points": _points(df)}  # fmt: skip


def _section(fn: Callable[[], Any]) -> dict[str, Any]:
    try:
        return {"ok": True, "result": fn()}
    except (stats.InsufficientData, ValueError, np.linalg.LinAlgError) as exc:
        return {"ok": False, "reason": str(exc)}


def analysis(
    conn: Connection, *, feature: str, asset: str, execution_lag_days: int = 0, max_lag: int = 8,
    rolling_window: int = 52, min_train: int = 104,
) -> dict[str, Any]:  # fmt: skip
    spec = FeatureSpec.parse(feature)
    f = compute_feature(conn, spec)
    p = market_prices(conn, asset)
    a = stats.align(f, p, spec.frequency, execution_lag_days=execution_lag_days)
    d = a.dropna(subset=["x", "y"])
    if d.empty:
        raise stats.InsufficientData("no overlapping, point-in-time aligned observations")
    return {
        "feature": spec.key,
        "asset": asset,
        "alignment": {
            "note": ALIGNMENT_NOTE,
            "n": len(d),
            "first": d.index[0].isoformat(),
            "last": d.index[-1].isoformat(),
            "median_staleness_days": float(d["staleness_days"].median()),
            "execution_lag_days": execution_lag_days,
        },
        "correlation": _section(lambda: stats.correlation(a)),
        "lagged_correlation": _section(lambda: stats.lagged_correlation(a, max_lag)),
        "rolling_correlation": _section(lambda: stats.rolling_correlation(a, rolling_window)),
        "stationarity": {
            "feature": _section(lambda: stats.stationarity(a["x"])),
            "returns": _section(lambda: stats.stationarity(a["y"])),
        },
        "regression": _section(lambda: stats.ols_hac(a)),
        "granger": _section(lambda: stats.granger(a, min(max_lag, 8))),
        "out_of_sample": _section(lambda: stats.out_of_sample(a, min_train)),
        "scatter": [
            {"x": round(float(r.x), 6), "y": round(float(r.y), 6), "t": t.isoformat()}
            for t, r in d.iterrows()
        ],
        "disclaimer": DISCLAIMER,
    }


def importance(conn: Connection, *, features: list[str], asset: str) -> dict[str, Any]:
    if not 2 <= len(features) <= 12:
        raise ValueError("choose between 2 and 12 features")
    specs = [FeatureSpec.parse(k) for k in features]
    freq = {s.frequency for s in specs}
    if len(freq) != 1:
        raise ValueError("all features must use the same frequency")
    prices = market_prices(conn, asset)
    base = stats.align(compute_feature(conn, specs[0]), prices, specs[0].frequency)
    X = pd.DataFrame(index=base.index)
    for s in specs:
        X[s.key] = pd.to_numeric(
            known_at(compute_feature(conn, s), base.index)["value"], errors="coerce"
        )
    res = stats.feature_importance(X, base["y"])
    return {"asset": asset, "test_r2": res.test_r2, "n_train": res.n_train, "n_test": res.n_test,
            "importances": res.importances, "method": "Ridge regression; permutation importance on the "
            "last 30% of periods (time-ordered split)", "disclaimer": DISCLAIMER}  # fmt: skip


_RUN_SUMMARY = """
    SELECT r.id, r.status, r.started_at, r.finished_at, r.error, r.code_version, r.data_snapshot_hash,
      (SELECT jsonb_object_agg(m.split || '.' || m.metric,
              jsonb_build_object('value', m.value, 'benchmark', m.benchmark_value))
         FROM backtest_metrics m WHERE m.backtest_run_id = r.id) AS metrics
    FROM backtest_runs r
"""


def experiments(conn: Connection, org_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = conn.execute(
        sa.text(
            f"""
            SELECT e.id, e.name, e.hypothesis, e.config, e.status, e.created_at, e.updated_at,
                   (SELECT count(*) FROM backtest_runs r WHERE r.experiment_id = e.id) AS n_runs,
                   (SELECT to_jsonb(x) FROM ({_RUN_SUMMARY} WHERE r.experiment_id = e.id
                      ORDER BY r.started_at DESC LIMIT 1) x) AS latest_run
            FROM research_experiments e WHERE e.organization_id = :org ORDER BY e.created_at DESC
            """
        ),
        {"org": org_id},
    ).all()
    return [dict(r._mapping) for r in rows]


def experiment_detail(
    conn: Connection, org_id: uuid.UUID, experiment_id: uuid.UUID
) -> dict[str, Any]:
    e = conn.execute(
        sa.text("SELECT id, name, hypothesis, config, status, created_at, updated_at "
                "FROM research_experiments WHERE id = :e AND organization_id = :org"),
        {"e": experiment_id, "org": org_id},
    ).one_or_none()  # fmt: skip
    if e is None:
        raise NotFound("experiment not found")
    runs = conn.execute(
        sa.text(
            f"""
            SELECT x.*, r.config, r.series, r.folds FROM ({_RUN_SUMMARY} WHERE r.experiment_id = :e) x
            JOIN backtest_runs r ON r.id = x.id ORDER BY x.started_at DESC LIMIT 10
            """
        ),
        {"e": experiment_id},
    ).all()
    return dict(e._mapping) | {"runs": [dict(r._mapping) for r in runs], "disclaimer": DISCLAIMER}


def create_and_run(
    engine: Engine, org_id: uuid.UUID, *, name: str, hypothesis: str, config: dict[str, Any]
) -> dict[str, Any]:
    cfg = ExperimentConfig(**config)
    cfg.validate()
    with engine.connect() as conn:
        exists = conn.execute(
            sa.text("SELECT 1 FROM indicator_series WHERE key = :k AND category = 'market'"),
            {"k": cfg.asset},
        ).scalar()
    if not exists:
        raise ValueError(f"unknown market series {cfg.asset!r}")
    with engine.begin() as conn:
        eid = create_experiment(conn, org_id, name=name, hypothesis=hypothesis, config=cfg)
    return rerun(engine, org_id, eid)


def rerun(engine: Engine, org_id: uuid.UUID, experiment_id: uuid.UUID) -> dict[str, Any]:
    with engine.connect() as conn:
        experiment_detail(conn, org_id, experiment_id)  # 404 before creating a run
    try:
        run_experiment(engine, experiment_id)
    except Exception:  # noqa: S110 - the failure is recorded on the run and returned below
        pass
    with engine.connect() as conn:
        return experiment_detail(conn, org_id, experiment_id)


_LATEST_RUNS = """
    SELECT DISTINCT ON (e.id) e.id AS experiment_id, e.name, r.id AS run_id, r.finished_at,
           r.series -> 'trial_sharpes' AS trial_sharpes,
           (r.series ->> 'n_configs_tested')::int AS n_configs,
           (SELECT jsonb_object_agg(m.metric, m.value) FROM backtest_metrics m
             WHERE m.backtest_run_id = r.id AND m.split = 'out_of_sample') AS oos
    FROM research_experiments e JOIN backtest_runs r ON r.experiment_id = e.id
    WHERE r.status = 'succeeded' AND e.organization_id = :org
    ORDER BY e.id, r.finished_at DESC
"""


def multiple_testing(conn: Connection, org_id: uuid.UUID) -> dict[str, Any]:
    """Deflated Sharpe ratios and FDR-adjusted p-values across every experiment's latest run.

    The trial count is every grid configuration searched by every experiment; the trial
    variance pools those configurations' full-sample per-period Sharpe ratios.
    """
    rows = [dict(r._mapping) for r in conn.execute(sa.text(_LATEST_RUNS), {"org": org_id}).all()]
    n_trials = sum(r["n_configs"] or 1 for r in rows)
    pooled = [float(v) for r in rows for v in (r["trial_sharpes"] or [])]
    trial_var = float(np.var(pooled, ddof=1)) if len(pooled) > 1 else None
    out = []
    for r in rows:
        oos = r["oos"] or {}
        sr, n = oos.get("sharpe_per_period"), oos.get("n_periods")
        skew, kurt = oos.get("skew"), oos.get("kurtosis")
        moments_known = skew is not None and kurt is not None
        res: dict[str, float | None] = {"sr_benchmark": None, "deflated_sharpe": None,
                                        "psr_zero": None}  # fmt: skip
        if sr is not None and n:
            sk = float(skew) if skew is not None else 0.0
            ku = float(kurt) if kurt is not None else 3.0
            if trial_var is not None:
                res = mt.deflated_sharpe(
                    float(sr), int(n), sk, ku, n_trials=n_trials, trial_var=trial_var
                )
            else:
                res["psr_zero"] = mt.probabilistic_sharpe(float(sr), 0.0, int(n), sk, ku)
        out.append({
            "experiment_id": r["experiment_id"], "name": r["name"], "run_id": r["run_id"],
            "n_periods": n, "sharpe_per_period": sr, "skew": skew, "kurtosis": kurt,
            "moments_assumed_normal": not moments_known, **res,
            "p_value": None if res["psr_zero"] is None else 1 - res["psr_zero"],
        })  # fmt: skip
    for item, adj in zip(out, mt.benjamini_hochberg([o["p_value"] for o in out]), strict=True):
        item["p_value_bh"] = adj
    return {
        "n_experiments": len(rows),
        "n_trials": n_trials,
        "trial_sharpe_variance": trial_var,
        "experiments": out,
        "method": (
            "Deflated Sharpe ratio (Bailey & López de Prado, 2014): the probability that the "
            "out-of-sample per-period Sharpe beats the Sharpe expected from the best of "
            "n_trials skill-less strategies. p-values are one-sided (1 - PSR vs 0), adjusted "
            "with Benjamini-Hochberg across experiments. Trials are correlated, so the "
            "correction is conservative. Runs made before moments were recorded assume "
            "normal returns; re-run them for exact values."
        ),
        "disclaimer": DISCLAIMER,
    }
