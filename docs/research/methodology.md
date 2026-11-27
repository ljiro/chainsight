# Quantitative research: methodology and limitations

ChainSight's research module (`ml/quant_research`, UI at `/research`) tests whether maritime
activity indicators lead market returns. It is a research environment, not a trading system,
and nothing it produces is investment advice.

## Data

| Source | Series | Access | Availability rule for bulk history |
|---|---|---|---|
| IMF PortWatch | Daily port calls and estimated import/export tonnes by vessel type, for an a priori set of 8 ports; daily transits and deadweight capacity for all 28 chokepoints; 2019 → | Open ArcGIS feature services, no key | Next Tuesday 12:00 UTC at least 4 days (ports) / 2 days (chokepoints) after the day, **plus 7 days** |
| FRED | Brent, WTI, Henry Hub daily spot prices (EIA via FRED) | Official API with `FRED_API_KEY`, otherwise FRED's public CSV download | 00:00 UTC on the day after the close |
| ChainSight AIS | Daily arrivals, departures, hours in port, route-deviation alerts | Own data | When ChainSight recorded each event, never before the day ends |

PortWatch figures are IMF estimates derived from satellite AIS, not customs statistics.
The port universe (Rotterdam, Singapore, Shanghai, Antwerp, Los Angeles–Long Beach, Houston,
Ras Tanura, Fujairah) was fixed by name before any results were looked at. Its IDs were
checked against PortWatch's own port database. That limits selection bias, but it is still a
researcher's choice.

### Why the availability rules look the way they do

PortWatch was observed publishing on Tuesday 24 Nov 2026 at 11:16 UTC, with ports through
Friday 20 Nov and chokepoints through Sunday 22 Nov. History loaded in bulk carries no record of
when each value was first published. So `available_at` comes from a rule that errs late: the
next qualifying Tuesday, then one more week in case a release slipped. A rule that is too late
only makes signals a week staler. A rule that is too early would leak the future into every
backtest.

Values ingested after the first load use the actual time ChainSight first saw them
(`available_at_assumed = false`).

## Point-in-time storage

- `indicator_observations` keeps every revision. A changed value is a new row
  (`revision + 1`) available from when it was seen, so nothing is overwritten. Each run
  re-fetches the last 60 days to catch revisions.
- **Historical signals use first-release values only.** A revision is information that arrived
  later and never leaks backwards.
- A derived value (weekly sum, ratio, z-score, year-over-year change) becomes available only
  when the last of its inputs did.
- `maritime_features` stores the default weekly dataset. Recomputations that change a value add
  a version; earlier versions stay.

## Alignment

For each period (weekly periods end on Friday):

1. **Decision time** is 00:00 UTC on the day after the period ends.
2. **x** is the newest feature value whose `available_at` is at or before the decision time.
   With the conservative PortWatch rule, the newest usable week is typically about two weeks
   old.
3. **Trade** at the close of the first trading day on or after the decision (optionally
   delayed further), and hold until the next decision's entry close.
4. **y** is the asset's log return over that holding period.

`y` is strictly after `x` was knowable. A test perturbs all data after a cutoff and asserts that
no earlier position changes.

## Statistics

| Test | Purpose | Caveat |
|---|---|---|
| Pearson / Spearman correlation | Association between x and the next return | Not adjusted for the many features and lags tried |
| Correlation by lag, with ±1.96/√n band | Lead-lag structure. Negative lags (returns leading) are diagnostics, not tradeable | About 1 in 20 lags exceeds the band by chance |
| Rolling correlation | Stability over time | A sign that flips is not a stable relationship |
| ADF and KPSS | Stationarity, judged on whether the two tests agree | Levels of activity are often non-stationary; use changes or z-scores |
| OLS with Newey–West (HAC) errors | Effect size with autocorrelation-robust inference | Linear only |
| Granger F-tests | Whether past x improves forecasts of y beyond past y | Requires stationary series; predictive association, not causation |
| Out-of-sample R² (Campbell–Thompson) | Expanding-window regression vs the historical-mean forecast | Below 0 means worse than predicting the mean |
| Permutation importance | Which of several features a ridge model relies on, measured on a later period | Importance is not significance |

## Backtests

- **Rule:** z-score the feature over a trailing window. Hold `direction × sign(z)` when
  |z| > threshold (or a position scaled by z), optionally long-only.
- **Walk-forward:**
  - Parameters (lookback ∈ {13, 26, 52}, threshold ∈ {0, 0.5, 1}, direction ∈ {+1, −1}; 18
    configurations) are chosen on each expanding training window by net Sharpe.
  - They are then applied unchanged to the next 26 weeks.
  - Only those out-of-sample weeks form the headline result.
- **In-sample:** parameters chosen and evaluated on all data. This is reported beside the
  out-of-sample result to show how much optimism the search adds.
- **Costs:** `cost_bps + slippage_bps` per unit of position change (default 5 + 5 bps).
- **Benchmark:** buy and hold the same asset over the same periods, with one entry cost.
- **Metrics:** Sharpe (no risk-free rate), Lo (2002) standard error and t-stat, total return,
  CAGR, volatility, maximum drawdown, Sortino, hit rate, exposure, annual turnover, and
  information ratio vs buy and hold. Per-period Sharpe, skewness and (raw) kurtosis are stored
  for the multiple-testing correction below.
- **Multiple-testing correction** (`ml/quant_research/multiple_testing.py`,
  `GET /api/v1/research/experiments/multiple-testing`):
  - Every run also stores the full-sample per-period Sharpe of each grid configuration it searched.
  - The **deflated Sharpe ratio** (Bailey & López de Prado, 2014) is the probabilistic Sharpe ratio
    of the out-of-sample returns, tested against the Sharpe expected from the best of `N` skill-less
    strategies. `N` is the number of configurations searched by *all* experiments (latest run of
    each). Their Sharpe variance is pooled. Skewness and kurtosis of the OOS returns widen the
    standard error.
  - One-sided p-values (1 − PSR vs 0) are adjusted with Benjamini–Hochberg across experiments.
  - The UI labels any result with a deflated Sharpe below 0.95 "No edge after deflation",
    whatever its t-stat.
  - Grid configurations are correlated, so the effective number of trials is smaller than `N`.
    The correction is therefore conservative. Runs made before moments were recorded assume
    normal returns and are flagged; re-run them for exact values.
- **Reproducibility:** each run stores the git commit, a SHA-256 of the exact inputs, the
  resolved configuration, per-fold choices and every position taken (`research_signals`).
  Identical inputs give identical runs.

## Seeded experiments (hypotheses written before running)

Results on the dev stack, 26 Nov 2026 (weekly, out-of-sample July 2021 – Nov 2026):

| Experiment | OOS Sharpe | Buy and hold | t-stat | Deflated SR | p (BH) | Reading |
|---|---|---|---|---|---|---|
| Hormuz + Bab el-Mandeb tanker transits (YoY) vs Brent | 0.13 | 0.23 | 0.30 | 0.03 | 0.58 | No edge |
| Suez share of Suez + Cape tanker transits (z26) vs Brent | −1.09 | 0.28 | −2.52 | 0.00 | 0.99 | Loses to buy and hold; the chosen direction flipped between regimes |
| Malacca tanker capacity (YoY) vs WTI | 0.27 | 0.18 | 0.59 | 0.06 | 0.58 | Not distinguishable from zero |

Deflated Sharpe ratios (27 Nov 2026) use 54 trials (3 experiments × 18 configurations). The
deflation benchmark is a per-period Sharpe of 0.139, about 1.0 annualized. Returns are
fat-tailed: OOS kurtosis is 5.6–19.

All three looked better in-sample (Sharpe 0.44–0.66). That gap is the point of the
walk-forward design. **None of these is evidence of a tradeable signal.**

## Known limitations

- **Short sample:** about 360 weekly observations since 2019. A Sharpe ratio's standard error
  over five years is about 0.45, so only very large effects are detectable.
- **Multiple testing:** every feature × transform × lag × parameter combination is another
  trial. Experiments are corrected with a deflated Sharpe ratio and Benjamini–Hochberg (above).
  Ad-hoc exploration in the analysis tab (correlations, Granger tests) is not counted as trials,
  so treat those p-values as descriptive.
- **First release only:** realistic, but slightly different from what a reader of today's
  revised data sees.
- **Simplified execution:** one asset per experiment, spot prices (not tradeable futures, with
  no roll yield), no borrow, and no capacity limits.
- **AIS-derived features:** ChainSight's own AIS history covers days, not years, so these are
  available for exploration but not yet meaningful in backtests.
