"""ETA models: the physics baseline and a quantile gradient-boosted model, behind one interface."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from ml.eta_prediction.features import FEATURES

BASELINE_VERSION = "baseline-distance-over-speed-v1"
BASELINE_INTERVAL = 0.2  # +/- 20 %, a heuristic band (not calibrated)


@dataclass(frozen=True)
class EtaEstimate:
    hours: float
    lower_hours: float | None
    upper_hours: float | None
    interval_level: float | None  # None = heuristic band, not a calibrated interval
    interval_method: str


class EtaModel(Protocol):
    def predict(self, X: pd.DataFrame) -> list[EtaEstimate]: ...


class BaselineEtaModel:
    """remaining distance / recent speed; needs no training data."""

    def predict(self, X: pd.DataFrame) -> list[EtaEstimate]:
        out = []
        for h in X["baseline_hours"].to_numpy(float):
            out.append(
                EtaEstimate(
                    h,
                    h * (1 - BASELINE_INTERVAL),
                    h * (1 + BASELINE_INTERVAL),
                    None,
                    "heuristic_pm20pct",
                )
            )
        return out


class QuantileGbmEtaModel:
    """Three HistGradientBoosting quantile regressors (p10 / p50 / p90) on hours-to-arrival.

    The p10-p90 band is a nominal 80 % interval; its empirical coverage is measured on the
    test split and in live evaluation rather than assumed."""

    QUANTILES = (0.1, 0.5, 0.9)

    def __init__(
        self, max_iter: int = 300, learning_rate: float = 0.05, random_state: int = 7
    ) -> None:
        self.params = {
            "max_iter": max_iter,
            "learning_rate": learning_rate,
            "random_state": random_state,
        }
        self.models: dict[float, HistGradientBoostingRegressor] = {}

    def fit(self, X: pd.DataFrame, y_hours: pd.Series) -> QuantileGbmEtaModel:
        for q in self.QUANTILES:
            m = HistGradientBoostingRegressor(loss="quantile", quantile=q, **self.params)
            self.models[q] = m.fit(X[FEATURES], y_hours)
        return self

    def predict(self, X: pd.DataFrame) -> list[EtaEstimate]:
        preds = {q: m.predict(X[FEATURES]) for q, m in self.models.items()}
        lo, mid, hi = (np.maximum(preds[q], 0.0) for q in self.QUANTILES)
        # Quantile crossing can happen with small data; enforce lo <= mid <= hi.
        lo, hi = np.minimum(lo, mid), np.maximum(hi, mid)
        return [
            EtaEstimate(float(m), float(a), float(b), 0.8, "quantile_gbm_p10_p90")
            for a, m, b in zip(lo, mid, hi, strict=True)
        ]
