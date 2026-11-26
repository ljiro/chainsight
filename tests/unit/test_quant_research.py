from __future__ import annotations

from datetime import UTC, date, datetime

import httpx
import numpy as np
import pandas as pd
import pytest

from integrations.research.availability import NextDay, WeeklyRelease, rule_from_dict
from integrations.research.fred import FredClient, parse_csv
from integrations.research.http import ResearchSourceError
from integrations.research.portwatch import PortWatchClient, parse_record
from ml.quant_research import stats
from ml.quant_research.backtest import BacktestConfig, metrics, net_returns, positions, run_backtest
from ml.quant_research.features import FeatureError, FeatureSpec, aggregate, apply_transform
from ml.quant_research.pit import known_at, snapshot_hash

UTC_ = "UTC"


def ts(s: str) -> pd.Timestamp:
    return pd.Timestamp(s, tz=UTC_)


# ---- availability rules -------------------------------------------------------------------


def test_weekly_release_is_next_qualifying_tuesday_plus_safety() -> None:
    rule = WeeklyRelease(weekday=1, hour=12, min_lag_days=4)
    # Fri 20 Nov -> earliest Tue 24 Nov -> +7 days safety.
    assert rule.available_at(date(2026, 11, 20)) == datetime(2026, 12, 1, 12, tzinfo=UTC)
    # Sat 14 Nov -> earliest Wed 18 Nov -> next Tue 24 Nov -> +7.
    assert rule.available_at(date(2026, 11, 14)) == datetime(2026, 12, 1, 12, tzinfo=UTC)
    # Never earlier than the observed release (Tue 24 Nov 11:16 UTC covered through Fri 20 Nov).
    assert WeeklyRelease(1, 12, 4, safety_days=0).available_at(date(2026, 11, 20)) >= datetime(
        2026, 11, 24, 11, 16, tzinfo=UTC
    )
    assert rule_from_dict(rule.as_dict()) == rule


def test_next_day_availability_for_closing_prices() -> None:
    assert NextDay().available_at(date(2026, 11, 24)) == datetime(2026, 11, 25, tzinfo=UTC)
    assert rule_from_dict({"kind": "next_day", "lag_days": 2}).available_at(
        date(2026, 11, 24)
    ) == datetime(2026, 11, 27, tzinfo=UTC)


# ---- point-in-time access -----------------------------------------------------------------


def test_known_at_never_uses_unpublished_values_and_handles_out_of_order_releases() -> None:
    f = pd.DataFrame(
        {
            "as_of": [ts("2026-01-02"), ts("2026-01-09"), ts("2026-01-16")],
            "value": [1.0, 2.0, 3.0],
            # Week 3 is published before week 2 (late correction of the order).
            "available_at": [ts("2026-01-05"), ts("2026-01-25"), ts("2026-01-20")],
        }
    )
    k = known_at(
        f,
        pd.DatetimeIndex([ts("2026-01-04"), ts("2026-01-06"), ts("2026-01-21"), ts("2026-01-26")]),
    )
    assert pd.isna(k["value"].iloc[0])  # nothing published yet
    assert k["value"].tolist()[1:] == [1.0, 3.0, 3.0]  # week 2 arriving late never replaces week 3
    assert k["staleness_days"].iloc[1] == 4


def test_snapshot_hash_changes_with_any_input_value() -> None:
    a = pd.DataFrame({"x": [1.0, 2.0]})
    b = pd.DataFrame({"x": [1.0, 2.5]})
    assert snapshot_hash(a) == snapshot_hash(a.copy()) != snapshot_hash(b)


# ---- feature engineering ------------------------------------------------------------------


def _daily(n: int, start: str = "2026-01-01", lag_days: int = 3) -> pd.DataFrame:
    days = pd.date_range(start, periods=n, freq="D", tz=UTC_)
    return pd.DataFrame({"as_of": days, "value": np.arange(1, n + 1, dtype=float),
                         "available_at": days + pd.Timedelta(days=lag_days)})  # fmt: skip


def test_weekly_aggregation_keeps_complete_weeks_and_latest_availability() -> None:
    d = _daily(15)  # Thu 1 Jan .. Thu 15 Jan 2026: the week ending Fri 16 Jan is incomplete
    w = aggregate(d, "W", "sum")
    assert list(w["as_of"].dt.strftime("%Y-%m-%d")) == ["2026-01-02", "2026-01-09"]
    assert w["value"].tolist() == [1 + 2, sum(range(3, 10))]
    assert w["available_at"].iloc[1] == ts("2026-01-12")  # last day (9 Jan) + 3 days


def test_transform_availability_spans_its_whole_window() -> None:
    p = pd.DataFrame({"as_of": pd.date_range("2026-01-02", periods=5, freq="W-FRI", tz=UTC_),
                      "value": [10.0, 11.0, 12.0, 13.0, 14.0],
                      "available_at": [ts("2026-01-10"), ts("2026-03-01"), ts("2026-01-24"),
                                       ts("2026-01-31"), ts("2026-02-07")]})  # fmt: skip
    out = apply_transform(p, FeatureSpec("x", "W", "pct1"))
    # Change at week 3 needs weeks 2 and 3; week 2 was published late (1 Mar).
    assert out.loc[out["as_of"] == ts("2026-01-16"), "available_at"].iloc[0] == ts("2026-03-01")
    z = apply_transform(p, FeatureSpec("x", "W", "z3"))
    assert len(z) == 3 and z["value"].iloc[-1] == pytest.approx(1.0)


def test_feature_spec_validation() -> None:
    assert FeatureSpec.parse("a|W|yoy").key == "a|W|yoy"
    for bad in ("a|W", "a|Q|level", "a|W|z0", "a|W|zz", "a|W|ma999"):
        with pytest.raises(FeatureError):
            FeatureSpec.parse(bad)


# ---- alignment and statistics -------------------------------------------------------------


def _prices(n_days: int, seed: int = 0, drift: float = 0.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2020-01-01", periods=n_days, tz=UTC_)
    px = 50 * np.exp(np.cumsum(rng.normal(drift, 0.02, n_days)))
    return pd.DataFrame({"as_of": days, "value": px, "available_at": days + pd.Timedelta(days=1)})


def _oracle_feature(prices: pd.DataFrame) -> pd.DataFrame:
    """A weekly feature equal to *next* week's return, published at the decision time.
    It is an oracle on purpose: it checks the plumbing, not a realistic signal."""
    a = stats.align(
        pd.DataFrame({"as_of": [prices["as_of"].iloc[0]], "value": [0.0],
                      "available_at": [prices["as_of"].iloc[0]]}),
        prices, "W",
    )  # fmt: skip
    ends = a.index - pd.Timedelta(days=1)
    return pd.DataFrame({"as_of": ends, "value": a["y"].to_numpy(), "available_at": a.index})


def test_align_returns_are_strictly_after_the_signal() -> None:
    prices = _prices(300)
    feature = pd.DataFrame({"as_of": pd.date_range("2020-01-03", periods=50, freq="W-FRI", tz=UTC_),
                            "value": np.arange(50, dtype=float)})  # fmt: skip
    feature["available_at"] = feature["as_of"] + pd.Timedelta(days=10)
    a = stats.align(feature, prices, "W")
    known = a.dropna(subset=["x"])
    assert (pd.to_datetime(known["x_available_at"], utc=True) <= known.index).all()
    assert (pd.to_datetime(known["entry_date"], utc=True) >= known.index.normalize()).all()
    # A Saturday decision enters at Monday's close and exits at the next Monday's close.
    row = a.loc[ts("2020-02-08")]
    assert row["entry_date"] == ts("2020-02-10") and row["exit_date"] == ts("2020-02-17")
    lagged = stats.align(feature, prices, "W", execution_lag_days=3)
    assert lagged.loc[ts("2020-02-08"), "entry_date"] == ts("2020-02-11")


def test_statistics_detect_a_planted_relationship_and_not_noise() -> None:
    rng = np.random.default_rng(1)
    idx = pd.date_range("2020-01-04", periods=300, freq="W-SAT", tz=UTC_)
    x = rng.normal(size=300)
    planted = pd.DataFrame({"x": x, "y": 0.5 * x + rng.normal(scale=0.5, size=300)}, index=idx)
    noise = pd.DataFrame({"x": x, "y": rng.normal(size=300)}, index=idx)
    assert stats.correlation(planted)["pearson_p"] < 1e-6
    assert stats.ols_hac(planted)["beta_p"] < 1e-6
    assert stats.out_of_sample(planted, 104)["r2_oos"] > 0.3
    assert stats.out_of_sample(noise, 104)["r2_oos"] < 0.05
    # y_t driven by x_{t-1}: past x improves forecasts of y.
    assert stats.granger(planted.assign(y=planted["y"].shift(1)).dropna(), 2)[0]["p"] < 1e-6
    lags = {r["lag"]: r["corr"] for r in stats.lagged_correlation(planted, 2)}
    assert lags[0] > 0.6 and abs(lags[2]) < 0.2


def test_stationarity_verdicts() -> None:
    rng = np.random.default_rng(2)
    assert stats.stationarity(pd.Series(rng.normal(size=400)))["verdict"] == "stationary"
    assert (
        stats.stationarity(pd.Series(np.cumsum(rng.normal(size=400))))["verdict"]
        == "non_stationary"
    )
    with pytest.raises(stats.InsufficientData):
        stats.stationarity(pd.Series([1.0, 2.0]))


def test_feature_importance_ranks_the_informative_feature_first() -> None:
    rng = np.random.default_rng(3)
    X = pd.DataFrame({"signal": rng.normal(size=400), "noise": rng.normal(size=400)})
    res = stats.feature_importance(X, 0.8 * X["signal"] + rng.normal(scale=0.3, size=400))
    assert res.importances[0]["feature"] == "signal" and res.test_r2 > 0.5


# ---- backtesting --------------------------------------------------------------------------

CFG = BacktestConfig(min_train_periods=80, test_periods=20, lookbacks=(13, 26), thresholds=(0.0,))


def test_oracle_signal_is_profitable_out_of_sample() -> None:
    prices = _prices(1500)
    a = stats.align(_oracle_feature(prices), prices, "W")
    res = run_backtest(a, CFG)
    oos = res.metrics["out_of_sample"]
    assert oos["sharpe"]["value"] > 3 and res.n_configs_tested == 4
    assert all(f["direction"] == 1 for f in res.folds)


def test_random_signal_shows_no_edge() -> None:
    prices = _prices(1500, seed=5)
    rng = np.random.default_rng(9)
    feat = pd.DataFrame({"as_of": pd.date_range("2020-01-03", periods=320, freq="W-FRI", tz=UTC_),
                         "value": rng.normal(size=320)})  # fmt: skip
    feat["available_at"] = feat["as_of"] + pd.Timedelta(days=1)
    res = run_backtest(stats.align(feat, prices, "W"), CFG)
    assert abs(res.metrics["out_of_sample"]["sharpe_t"]["value"]) < 3


def test_walk_forward_positions_do_not_depend_on_future_data() -> None:
    prices = _prices(1500, seed=4)
    rng = np.random.default_rng(4)
    feat = pd.DataFrame({"as_of": pd.date_range("2020-01-03", periods=320, freq="W-FRI", tz=UTC_),
                         "value": rng.normal(size=320)})  # fmt: skip
    feat["available_at"] = feat["as_of"] + pd.Timedelta(days=1)
    base = run_backtest(stats.align(feat, prices, "W"), CFG)
    cutoff = ts(base.series[len(base.series) // 2]["t"])
    # Rewrite everything after the cutoff: the future must not change past positions.
    feat2 = feat.copy()
    feat2.loc[feat2["available_at"] > cutoff, "value"] *= -5
    prices2 = prices.copy()
    prices2.loc[prices2["as_of"] > cutoff, "value"] *= 3
    other = run_backtest(stats.align(feat2, prices2, "W"), CFG)
    before = [p["position"] for p in base.series if ts(p["t"]) <= cutoff]
    assert before == [p["position"] for p in other.series if ts(p["t"]) <= cutoff][: len(before)]


def test_costs_are_charged_on_position_changes() -> None:
    idx = pd.date_range("2026-01-03", periods=4, freq="W-SAT", tz=UTC_)
    pos = pd.Series([1.0, -1.0, -1.0, 0.0], index=idx)
    y = pd.Series([0.0, 0.0, 0.0, 0.0], index=idx)
    cfg = BacktestConfig(cost_bps=10, slippage_bps=10, min_train_periods=80, lookbacks=(13,))
    r = net_returns(pos, y, cfg)
    assert r.tolist() == pytest.approx([-0.002, -0.004, 0.0, -0.002])


def test_long_only_and_linear_sizing() -> None:
    x = pd.Series(np.r_[np.zeros(30), np.linspace(-3, 3, 30)])
    cfg = BacktestConfig(sizing="linear", long_only=True, min_train_periods=80, lookbacks=(13,))
    p = positions(x, 13, 0.0, 1, cfg)
    assert p.min() >= 0 and p.max() <= 1


def test_metrics_drawdown_and_benchmark() -> None:
    r = pd.Series([0.1, -0.5, 0.2])
    m = metrics(r, pd.Series([1.0, 1.0, 1.0]), 52, benchmark=pd.Series([0.0, 0.0, 0.0]))
    assert m["max_drawdown"] == pytest.approx(-0.5)
    assert m["total_return"] == pytest.approx(1.1 * 0.5 * 1.2 - 1)


def test_config_validation() -> None:
    with pytest.raises(ValueError):
        BacktestConfig(min_train_periods=20, lookbacks=(52,))
    with pytest.raises(ValueError):
        BacktestConfig(directions=(2,))


# ---- source adapters ----------------------------------------------------------------------


def test_fred_csv_parsing_skips_missing_values() -> None:
    text = (
        "observation_date,DCOILBRENTEU\n2026-11-20,80.1\n2026-11-21,\n2026-11-24,.\n2026-11-25,81\n"
    )
    assert parse_csv(text, "DCOILBRENTEU") == [
        (date(2026, 11, 20), 80.1),
        (date(2026, 11, 25), 81.0),
    ]
    with pytest.raises(ResearchSourceError):
        parse_csv("DATE,OTHER\n", "DCOILBRENTEU")


def test_fred_api_key_is_never_in_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error_message": "bad"})

    client = FredClient("secret-key-123", client=httpx.Client(transport=httpx.MockTransport(handler)),
                        sleep=lambda _: None)  # fmt: skip
    with pytest.raises(ResearchSourceError) as exc:
        client.observations("DCOILBRENTEU")
    assert "secret-key-123" not in str(exc.value)


def test_portwatch_paging_and_parsing() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["resultOffset"])
        calls.append(offset)
        feats = [{"attributes": {"date": f"2026-11-{offset + i + 1:02d}", "portid": "port1114",
                                 "portname": "Rotterdam", "ISO3": "NLD", "portcalls": 70 + i,
                                 "portcalls_tanker": None}} for i in range(2)]  # fmt: skip
        return httpx.Response(200, json={"features": feats, "exceededTransferLimit": offset == 0})

    client = PortWatchClient(
        client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda _: None
    )
    recs = list(client.daily("port", ["port1114"], date(2026, 11, 1)))
    assert calls == [0, 2] and len(recs) == 4
    assert recs[0].metrics == {"portcalls": 70.0}  # nulls are missing, not zero
    with pytest.raises(ValueError):
        list(client.daily("port", ["x' OR '1'='1"]))


def test_portwatch_error_payload_raises() -> None:
    client = PortWatchClient(
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(200, json={"error": {"code": 400}})
            )
        ),
        sleep=lambda _: None,
    )
    with pytest.raises(ResearchSourceError):
        client.entities("chokepoint")
    rec = parse_record("chokepoint", {"date": "2026-11-22", "portid": "chokepoint1", "portname": "Suez Canal",
                                      "n_tanker": 12})  # fmt: skip
    assert (
        rec.day == date(2026, 11, 22)
        and rec.metrics == {"n_tanker": 12.0}
        and rec.country_iso3 is None
    )
