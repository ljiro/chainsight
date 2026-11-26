"""Statistical research on point-in-time aligned data.

`align` builds the only frame the other functions accept. Each row is a decision time
(the day after a period ends, 00:00 UTC) with:

- `x`: the feature value known at that decision time (point-in-time, see pit.py);
- `y`: the asset's log return from the close of the first trading day on or after the
  decision (plus any extra execution lag) to the close at the next decision's entry.

So `y` is strictly in the future of `x`: a non-zero relationship at lag 0 is, in principle,
tradeable, before costs. Nothing here is investment advice, and with dozens of features and
lags some "significant" results are expected by chance alone.
"""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats as sps
from sklearn.inspection import permutation_importance
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from ml.quant_research.features import FREQUENCIES
from ml.quant_research.pit import known_at, utc_ns

MIN_OBS = 20


class InsufficientData(ValueError):
    pass


def decision_times(start: pd.Timestamp, end: pd.Timestamp, frequency: str) -> pd.DatetimeIndex:
    """Decision time = 00:00 UTC on the day after each period end."""
    ends = pd.date_range(start.normalize(), end.normalize(), freq=FREQUENCIES[frequency], tz="UTC")
    return pd.DatetimeIndex(ends + pd.Timedelta(days=1)).as_unit("ns")


def align(
    feature: pd.DataFrame,
    prices: pd.DataFrame,
    frequency: str,
    *,
    execution_lag_days: int = 0,
) -> pd.DataFrame:
    """Point-in-time aligned frame indexed by decision time: x, x_as_of, staleness_days,
    entry_date, exit_date, y (forward log return)."""
    if feature.empty or prices.empty:
        raise InsufficientData("feature or prices are empty")
    px = prices.dropna(subset=["value"]).sort_values("as_of")
    px = px[px["value"] > 0]
    days = pd.DatetimeIndex(utc_ns(px["as_of"]))
    values = px["value"].to_numpy(dtype=float)
    start = max(utc_ns(feature["available_at"]).min(), days.min())
    dts = decision_times(start, days.max(), frequency)
    known = known_at(feature, dts)
    # Entry: close of the first trading day on or after decision day (+ extra lag).
    entry_day = dts.normalize() + pd.Timedelta(days=execution_lag_days)
    idx = days.searchsorted(entry_day, side="left")
    valid = idx < len(days)
    df = pd.DataFrame(index=dts)
    df["x"] = pd.to_numeric(known["value"], errors="coerce").to_numpy()
    df["x_as_of"] = known["as_of"].to_numpy()
    df["x_available_at"] = known["available_at"].to_numpy()
    df["staleness_days"] = pd.to_numeric(known["staleness_days"], errors="coerce").to_numpy()
    entry_pos = np.where(valid, idx, -1)
    df["entry_date"] = [days[i] if i >= 0 else pd.NaT for i in entry_pos]
    entry_px = np.array([values[i] if i >= 0 else np.nan for i in entry_pos])
    exit_px = np.roll(entry_px, -1)
    exit_px[-1] = np.nan
    df["exit_date"] = df["entry_date"].shift(-1)
    # Two decisions mapping to the same entry day (e.g. holidays) hold for zero time.
    df["y"] = np.log(exit_px / entry_px)
    df = df[df["entry_date"].notna()]
    return df


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    d = df[["x", "y"]].dropna()
    if len(d) < MIN_OBS:
        raise InsufficientData(f"only {len(d)} aligned observations (need {MIN_OBS})")
    return d


def _f(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else round(f, 6)


def correlation(df: pd.DataFrame) -> dict[str, Any]:
    d = _clean(df)
    pr = sps.pearsonr(d["x"], d["y"])
    sr = sps.spearmanr(d["x"], d["y"])
    return {"n": len(d), "pearson": _f(pr.statistic), "pearson_p": _f(pr.pvalue),
            "spearman": _f(sr.statistic), "spearman_p": _f(sr.pvalue)}  # fmt: skip


def rolling_correlation(df: pd.DataFrame, window: int) -> list[dict[str, Any]]:
    d = df[["x", "y"]].dropna()
    r = d["x"].rolling(window, min_periods=window).corr(d["y"]).dropna()
    return [{"t": t.isoformat(), "value": _f(v)} for t, v in r.items()]


def lagged_correlation(df: pd.DataFrame, max_lag: int) -> list[dict[str, Any]]:
    """corr(x_t, y_{t+k}). k >= 0: the feature leads the return (k = 0 is the next period).
    k < 0: the return leads the feature (diagnostic only, not tradeable)."""
    d = df[["x", "y"]].dropna()
    out = []
    for k in range(-max_lag, max_lag + 1):
        pair = pd.concat([d["x"], d["y"].shift(-k)], axis=1).dropna()
        if len(pair) < MIN_OBS:
            continue
        r = sps.pearsonr(pair.iloc[:, 0], pair.iloc[:, 1])
        out.append({"lag": k, "corr": _f(r.statistic), "p": _f(r.pvalue), "n": len(pair),
                    "ci95": _f(1.96 / math.sqrt(len(pair)))})  # fmt: skip
    return out


def stationarity(series: pd.Series) -> dict[str, Any]:
    """ADF (H0: unit root) and KPSS (H0: stationary). Agreement is what matters."""
    from statsmodels.tsa.stattools import adfuller, kpss

    s = series.dropna().astype(float)
    if len(s) < MIN_OBS:
        raise InsufficientData("too few observations for stationarity tests")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        adf = adfuller(s, autolag="AIC")
        kp = kpss(s, regression="c", nlags="auto")
    adf_p, kpss_p = float(adf[1]), float(kp[1])
    verdict = (
        "stationary" if adf_p < 0.05 and kpss_p > 0.05
        else "non_stationary" if adf_p >= 0.05 and kpss_p <= 0.05
        else "inconclusive"
    )  # fmt: skip
    return {"n": len(s), "adf_stat": _f(adf[0]), "adf_p": _f(adf_p), "kpss_stat": _f(kp[0]),
            "kpss_p": _f(kpss_p), "verdict": verdict,
            "note": "KPSS p-values are interpolated and capped at 0.01-0.10."}  # fmt: skip


def ols_hac(df: pd.DataFrame) -> dict[str, Any]:
    """y = a + b x with Newey-West (HAC) standard errors."""
    import statsmodels.api as sm

    d = _clean(df)
    lags = int(4 * (len(d) / 100) ** (2 / 9))
    model = sm.OLS(d["y"], sm.add_constant(d["x"])).fit(cov_type="HAC", cov_kwds={"maxlags": lags})
    return {"n": len(d), "alpha": _f(model.params["const"]), "beta": _f(model.params["x"]),
            "beta_t": _f(model.tvalues["x"]), "beta_p": _f(model.pvalues["x"]),
            "r2": _f(model.rsquared), "hac_lags": lags}  # fmt: skip


def granger(df: pd.DataFrame, max_lag: int) -> list[dict[str, Any]]:
    """Does past x improve forecasts of y beyond past y? (F-test per lag order). Valid only for
    stationary series; a small p-value is predictive association, not causation."""
    from statsmodels.tsa.stattools import grangercausalitytests

    d = _clean(df)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        res = grangercausalitytests(d[["y", "x"]].to_numpy(), maxlag=max_lag)
    return [{"lag": int(k), "f": _f(r[0]["ssr_ftest"][0]), "p": _f(r[0]["ssr_ftest"][1])}
            for k, r in sorted(res.items())]  # fmt: skip


def out_of_sample(df: pd.DataFrame, min_train: int) -> dict[str, Any]:
    """Expanding-window OLS forecasts of y from x vs the historical-mean forecast.
    R²_oos > 0 means the feature beat the mean out of sample (Campbell & Thompson, 2008)."""
    d = _clean(df)
    if len(d) <= min_train + 5:
        raise InsufficientData("not enough data after the training window")
    x, y = d["x"].to_numpy(), d["y"].to_numpy()
    preds, bench, actual = [], [], []
    for t in range(min_train, len(d)):
        xs, ys = x[:t], y[:t]
        b, a = np.polyfit(xs, ys, 1) if np.std(xs) > 0 else (0.0, ys.mean())
        preds.append(a + b * x[t])
        bench.append(ys.mean())
        actual.append(y[t])
    p, m, a_ = np.array(preds), np.array(bench), np.array(actual)
    sse, sse_mean = float(((a_ - p) ** 2).sum()), float(((a_ - m) ** 2).sum())
    return {"n_test": len(a_), "r2_oos": _f(1 - sse / sse_mean) if sse_mean > 0 else None,
            "hit_rate": _f((np.sign(p) == np.sign(a_)).mean()),
            "hit_rate_mean_forecast": _f((np.sign(m) == np.sign(a_)).mean()),
            "min_train": min_train}  # fmt: skip


@dataclass
class ImportanceResult:
    test_r2: float | None
    n_train: int
    n_test: int
    importances: list[dict[str, Any]]


def feature_importance(
    X: pd.DataFrame, y: pd.Series, test_fraction: float = 0.3
) -> ImportanceResult:
    """Permutation importance of a ridge model on a later, held-out period."""
    d = pd.concat([X, y.rename("_y")], axis=1).dropna()
    if len(d) < MIN_OBS * 2:
        raise InsufficientData(f"only {len(d)} complete rows")
    cut = int(len(d) * (1 - test_fraction))
    train, test = d.iloc[:cut], d.iloc[cut:]
    cols = list(X.columns)
    model = make_pipeline(StandardScaler(), Ridge(alpha=1.0)).fit(train[cols], train["_y"])
    pi = permutation_importance(model, test[cols], test["_y"], n_repeats=30, random_state=7,
                                scoring="neg_mean_squared_error")  # fmt: skip
    coefs = model[-1].coef_
    imps = [{"feature": c, "importance": _f(pi.importances_mean[i]), "std": _f(pi.importances_std[i]),
             "coef_std": _f(coefs[i])} for i, c in enumerate(cols)]  # fmt: skip
    imps.sort(key=lambda r: -(r["importance"] or 0))
    ss_res = float(((test["_y"] - model.predict(test[cols])) ** 2).sum())
    ss_tot = float(((test["_y"] - train["_y"].mean()) ** 2).sum())
    return ImportanceResult(
        _f(1 - ss_res / ss_tot) if ss_tot > 0 else None, len(train), len(test), imps
    )
