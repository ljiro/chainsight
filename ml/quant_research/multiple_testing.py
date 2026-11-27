"""Multiple-testing corrections for backtested Sharpe ratios.

Every experiment searches a parameter grid, and every new experiment is another trial. The
best of many noisy Sharpe ratios is biased upwards, so a raw Sharpe (or its Lo t-stat) is
not evidence by itself. Two corrections are reported:

- **Probabilistic Sharpe ratio** (Bailey & López de Prado, 2012): the probability that the
  true per-period Sharpe exceeds a benchmark `sr_star`, given the sample length, skewness
  and kurtosis of the returns.
- **Deflated Sharpe ratio** (Bailey & López de Prado, 2014): the PSR with `sr_star` set to the
  Sharpe expected from the *best* of `n_trials` independent strategies with no skill, whose
  Sharpe ratios vary with variance `trial_var`.

`benjamini_hochberg` adjusts the experiments' one-sided p-values (1 - PSR(0)) for the false
discovery rate across experiments.

All Sharpe ratios here are **per period** (not annualized), as the formulas require.
Independence of trials is an approximation: grid configurations are correlated, so the
effective number of trials is smaller than the count used, making the correction
conservative.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
from scipy import stats as sps

EULER_GAMMA = 0.5772156649015329


def probabilistic_sharpe(
    sr: float, sr_star: float, n: int, skew: float = 0.0, kurtosis: float = 3.0
) -> float | None:
    """P(true SR > sr_star). `kurtosis` is raw (normal = 3), not excess."""
    if n < 2 or not all(map(math.isfinite, (sr, sr_star, skew, kurtosis))):
        return None
    var = 1 - skew * sr + (kurtosis - 1) / 4 * sr**2
    if var <= 0:
        return None
    return float(sps.norm.cdf((sr - sr_star) * math.sqrt(n - 1) / math.sqrt(var)))


def expected_max_sharpe(n_trials: int, trial_var: float) -> float:
    """E[max SR] over `n_trials` skill-less strategies whose SRs have variance `trial_var`."""
    if n_trials < 1 or trial_var < 0:
        raise ValueError("n_trials must be >= 1 and trial_var >= 0")
    if n_trials == 1 or trial_var == 0:
        return 0.0
    z = sps.norm.ppf
    return float(
        math.sqrt(trial_var)
        * ((1 - EULER_GAMMA) * z(1 - 1 / n_trials) + EULER_GAMMA * z(1 - 1 / (n_trials * math.e)))
    )


def deflated_sharpe(
    sr: float, n: int, skew: float, kurtosis: float, *, n_trials: int, trial_var: float
) -> dict[str, float | None]:
    sr0 = expected_max_sharpe(n_trials, trial_var)
    return {
        "sr_benchmark": sr0,
        "deflated_sharpe": probabilistic_sharpe(sr, sr0, n, skew, kurtosis),
        "psr_zero": probabilistic_sharpe(sr, 0.0, n, skew, kurtosis),
    }


def benjamini_hochberg(p_values: Sequence[float | None]) -> list[float | None]:
    """FDR-adjusted p-values (step-up). `None` entries are ignored and kept as `None`."""
    idx = [i for i, p in enumerate(p_values) if p is not None and math.isfinite(p)]
    out: list[float | None] = [None] * len(p_values)
    if not idx:
        return out
    p = np.array([p_values[i] for i in idx], dtype=float)
    m = len(p)
    order = np.argsort(p)
    ranked = p[order] * m / np.arange(1, m + 1)
    adjusted = np.minimum.accumulate(ranked[::-1])[::-1].clip(max=1.0)
    for rank, pos in enumerate(order):
        out[idx[pos]] = float(adjusted[rank])
    return out
