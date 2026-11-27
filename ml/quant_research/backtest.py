"""Walk-forward backtesting of single-feature timing rules on one asset.

Rule: z-score the feature against its trailing `lookback` periods (data known at each
decision only), then hold `direction * sign(z)` when |z| > threshold (`sizing="sign"`) or
`direction * clip(z / 2, -1, 1)` (`sizing="linear"`); flat otherwise. `long_only` clips at 0.

Walk-forward: parameters (lookback, threshold, direction) are chosen on each expanding
training window by net Sharpe, then applied unchanged to the next `test_periods`. Only those
out-of-sample periods form the reported OOS result. The in-sample result (parameters chosen
and evaluated on all data) is reported separately and is optimistic by construction.

Costs: `cost_bps + slippage_bps` per unit of position change, charged when the position
changes. Benchmark: buy-and-hold the asset over the same periods (one entry cost).
"""

from __future__ import annotations

import itertools
import math
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from ml.quant_research.features import PERIODS_PER_YEAR

TRADING_PERIODS = {"D": 252, "W": 52, "M": 12}


@dataclass(frozen=True)
class BacktestConfig:
    frequency: str = "W"
    sizing: str = "sign"  # sign | linear
    long_only: bool = False
    cost_bps: float = 5.0
    slippage_bps: float = 5.0
    min_train_periods: int = 104
    test_periods: int = 26
    lookbacks: tuple[int, ...] = (13, 26, 52)
    thresholds: tuple[float, ...] = (0.0, 0.5, 1.0)
    directions: tuple[int, ...] = (1, -1)

    def __post_init__(self) -> None:
        if self.sizing not in ("sign", "linear"):
            raise ValueError("sizing must be 'sign' or 'linear'")
        if self.frequency not in PERIODS_PER_YEAR:
            raise ValueError("unknown frequency")
        if self.min_train_periods < max(self.lookbacks) + 10:
            raise ValueError("min_train_periods must exceed the longest lookback by 10+")
        if (
            self.test_periods < 1
            or not self.lookbacks
            or not self.thresholds
            or not self.directions
        ):
            raise ValueError("empty parameter grid or test window")
        if any(d not in (1, -1) for d in self.directions):
            raise ValueError("directions must be 1 and/or -1")

    @property
    def grid(self) -> list[tuple[int, float, int]]:
        return list(itertools.product(self.lookbacks, self.thresholds, self.directions))

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BacktestResult:
    config: dict[str, Any]
    n_configs_tested: int
    in_sample_params: dict[str, Any]
    metrics: dict[str, dict[str, dict[str, float | None]]]  # split -> metric -> {value, benchmark}
    folds: list[dict[str, Any]] = field(default_factory=list)
    series: list[dict[str, Any]] = field(default_factory=list)
    # Full-sample, per-period net Sharpe of every grid configuration (the trials searched);
    # their dispersion feeds the deflated Sharpe ratio (multiple_testing.py).
    trial_sharpes: list[float] = field(default_factory=list)


def zscore(x: pd.Series, lookback: int) -> pd.Series:
    """Trailing z-score: uses x[t-lookback+1 .. t], all known at decision t."""
    mu = x.rolling(lookback, min_periods=lookback).mean()
    sd = x.rolling(lookback, min_periods=lookback).std()
    return ((x - mu) / sd).where(sd > 0)


def positions(
    x: pd.Series, lookback: int, threshold: float, direction: int, cfg: BacktestConfig
) -> pd.Series:
    z = zscore(x, lookback)
    if cfg.sizing == "sign":
        pos = np.where(z.abs() > threshold, direction * np.sign(z), 0.0)
    else:
        pos = np.where(z.abs() > threshold, np.clip(direction * z / 2, -1, 1), 0.0)
    p = pd.Series(pos, index=x.index).fillna(0.0)
    if cfg.long_only:
        p = p.clip(lower=0.0)
    return p


def net_returns(pos: pd.Series, y: pd.Series, cfg: BacktestConfig) -> pd.Series:
    """Per-period net simple return of holding `pos[t]` over period t (y is the log return
    from entry at decision t to entry at t+1); trading cost when the position changes."""
    simple = np.expm1(y)
    turnover = pos.diff().abs().fillna(pos.abs())
    cost = (cfg.cost_bps + cfg.slippage_bps) / 1e4
    return pos * simple - cost * turnover


def _sharpe(r: pd.Series, ppy: int) -> float:
    sd = r.std()
    return float(r.mean() / sd * math.sqrt(ppy)) if len(r) > 2 and sd > 0 else float("-inf")


def metrics(
    r: pd.Series, pos: pd.Series, ppy: int, benchmark: pd.Series | None = None
) -> dict[str, float | None]:
    r = r.dropna()
    n = len(r)
    if n == 0:
        return {"n_periods": 0}
    eq = (1 + r).cumprod()
    total = float(eq.iloc[-1] - 1)
    years = n / ppy
    sd = float(r.std()) if n > 1 else 0.0
    sharpe = float(r.mean() / sd * math.sqrt(ppy)) if sd > 0 else None
    downside = r[r < 0].std()
    dd = eq / eq.cummax() - 1
    sr_p = (r.mean() / sd) if sd > 0 else 0.0
    # Lo (2002) standard error of the per-period Sharpe, annualized (iid approximation).
    se = math.sqrt((1 + 0.5 * sr_p**2) / n) * math.sqrt(ppy) if n > 1 else None
    out: dict[str, float | None] = {
        "n_periods": float(n),
        "total_return": total,
        "cagr": float(eq.iloc[-1] ** (1 / years) - 1) if years > 0 and eq.iloc[-1] > 0 else None,
        "ann_vol": sd * math.sqrt(ppy),
        "sharpe": sharpe,
        "sharpe_per_period": float(sr_p) if sd > 0 else None,
        "skew": float(r.skew()) if n > 2 and sd > 0 else None,
        # Raw kurtosis (normal = 3), as the probabilistic Sharpe ratio expects.
        "kurtosis": float(r.kurt() + 3) if n > 3 and sd > 0 else None,
        "sharpe_se": se,
        "sharpe_t": (sharpe / se) if sharpe is not None and se else None,
        "sortino": float(r.mean() / downside * math.sqrt(ppy))
        if downside and downside > 0
        else None,
        "max_drawdown": float(dd.min()),
        "hit_rate": float((r[pos.reindex(r.index).abs() > 0] > 0).mean())
        if (pos.abs() > 0).any()
        else None,
        "exposure": float(pos.reindex(r.index).abs().mean()),
        "turnover_ann": float(pos.reindex(r.index).diff().abs().fillna(0).mean() * ppy),
    }
    if benchmark is not None:
        b = benchmark.reindex(r.index).fillna(0.0)
        active = r - b
        asd = active.std()
        out["information_ratio"] = float(active.mean() / asd * math.sqrt(ppy)) if asd > 0 else None
        out["corr_to_benchmark"] = float(r.corr(b)) if r.std() > 0 and b.std() > 0 else None
    return {k: (None if v is None or (isinstance(v, float) and not math.isfinite(v)) else round(v, 6))
            for k, v in out.items()}  # fmt: skip


def _best(
    x: pd.Series, y: pd.Series, mask: pd.Series, cfg: BacktestConfig, ppy: int
) -> tuple[tuple[int, float, int], float]:
    best, best_s = cfg.grid[0], float("-inf")
    for params in cfg.grid:
        r = net_returns(positions(x, *params, cfg), y, cfg)[mask]
        s = _sharpe(r.dropna(), ppy)
        if s > best_s:
            best, best_s = params, s
    return best, best_s


def run_backtest(frame: pd.DataFrame, cfg: BacktestConfig) -> BacktestResult:
    """`frame` comes from stats.align: indexed by decision time with columns x and y."""
    d = frame[["x", "y"]].copy()
    d = d[d["y"].notna()]
    d["x"] = d["x"].ffill()  # carry the last known value; never fills backwards
    d = d[d["x"].notna()]
    n = len(d)
    if n < cfg.min_train_periods + cfg.test_periods:
        raise ValueError(f"{n} periods; need at least {cfg.min_train_periods + cfg.test_periods}")
    ppy = TRADING_PERIODS[cfg.frequency]
    x, y = d["x"], d["y"]
    bench_ret = np.expm1(y)
    bench_ret.iloc[0] -= (cfg.cost_bps + cfg.slippage_bps) / 1e4

    # Walk-forward out-of-sample positions.
    oos_pos = pd.Series(np.nan, index=d.index)
    folds = []
    start = cfg.min_train_periods
    while start < n:
        end = min(start + cfg.test_periods, n)
        train_mask = pd.Series(False, index=d.index)
        train_mask.iloc[:start] = True
        params, train_sharpe = _best(x, y, train_mask, cfg, ppy)
        oos_pos.iloc[start:end] = positions(x, *params, cfg).iloc[start:end]
        folds.append({
            "train_start": d.index[0].isoformat(), "train_end": d.index[start - 1].isoformat(),
            "test_start": d.index[start].isoformat(), "test_end": d.index[end - 1].isoformat(),
            "lookback": params[0], "threshold": params[1], "direction": params[2],
            "train_sharpe": round(train_sharpe, 4) if math.isfinite(train_sharpe) else None,
        })  # fmt: skip
        start = end
    oos_idx = oos_pos.dropna().index
    pos_oos = oos_pos.loc[oos_idx]
    r_oos = net_returns(pos_oos, y.loc[oos_idx], cfg)
    b_oos = np.expm1(y.loc[oos_idx]).copy()
    b_oos.iloc[0] -= (cfg.cost_bps + cfg.slippage_bps) / 1e4

    # In-sample (optimistic): best parameters on all data, evaluated on all data.
    all_mask = pd.Series(True, index=d.index)
    is_params, _ = _best(x, y, all_mask, cfg, ppy)
    trial_sharpes = []
    for params in cfg.grid:
        tr = net_returns(positions(x, *params, cfg), y, cfg).dropna()
        tsd = tr.std()
        if len(tr) > 2 and tsd > 0:
            trial_sharpes.append(round(float(tr.mean() / tsd), 6))
    pos_is = positions(x, *is_params, cfg)
    r_is = net_returns(pos_is, y, cfg)

    def pair(r: pd.Series, p: pd.Series, b: pd.Series) -> dict[str, dict[str, float | None]]:
        m = metrics(r, p, ppy, benchmark=b)
        bm = metrics(b, pd.Series(1.0, index=b.index), ppy)
        return {k: {"value": v, "benchmark": bm.get(k)} for k, v in m.items()}

    eq = (1 + r_oos).cumprod()
    beq = (1 + b_oos).cumprod()
    series = [
        {"t": t.isoformat(), "position": round(float(pos_oos[t]), 4), "signal": _r(x[t]),
         "net_return": round(float(r_oos[t]), 6), "equity": round(float(eq[t]), 6),
         "benchmark_equity": round(float(beq[t]), 6),
         "drawdown": round(float(eq[t] / eq.loc[:t].max() - 1), 6)}
        for t in oos_idx
    ]  # fmt: skip
    return BacktestResult(
        config=cfg.as_dict(),
        n_configs_tested=len(cfg.grid),
        in_sample_params={
            "lookback": is_params[0],
            "threshold": is_params[1],
            "direction": is_params[2],
        },
        metrics={
            "in_sample": pair(r_is, pos_is, bench_ret),
            "out_of_sample": pair(r_oos, pos_oos, b_oos),
        },
        folds=folds,
        series=series,
        trial_sharpes=trial_sharpes,
    )


def _r(v: float) -> float | None:
    return None if v is None or not math.isfinite(float(v)) else round(float(v), 6)
