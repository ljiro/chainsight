"""Evaluation metrics (all returned as plain floats so they serialize to JSONB/MLflow)."""

from __future__ import annotations

import itertools
import math

import numpy as np
from numpy.typing import ArrayLike
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)


def _f(x: float) -> float | None:
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(float(x), 4)


def eta_metrics(
    actual_hours: ArrayLike,
    predicted_hours: ArrayLike,
    lower_hours: ArrayLike | None = None,
    upper_hours: ArrayLike | None = None,
) -> dict[str, float | None]:
    """Errors in hours: MAE, median AE, RMSE, share within 6/12/24 h, interval coverage."""
    a, p = np.asarray(actual_hours, float), np.asarray(predicted_hours, float)
    if a.size == 0:
        raise ValueError("no samples")
    err = np.abs(p - a)
    out: dict[str, float | None] = {
        "mae_hours": _f(err.mean()),
        "median_ae_hours": _f(np.median(err)),
        "rmse_hours": _f(math.sqrt(float(np.mean((p - a) ** 2)))),
        "within_6h": _f((err <= 6).mean()),
        "within_12h": _f((err <= 12).mean()),
        "within_24h": _f((err <= 24).mean()),
        "bias_hours": _f((p - a).mean()),
    }
    if lower_hours is not None and upper_hours is not None:
        lo, hi = np.asarray(lower_hours, float), np.asarray(upper_hours, float)
        out["interval_coverage"] = _f(((a >= lo) & (a <= hi)).mean())
        out["mean_interval_width_hours"] = _f((hi - lo).mean())
    return out


def calibration_bins(
    y: ArrayLike, prob: ArrayLike, n_bins: int = 10
) -> list[dict[str, float | int]]:
    y_arr, p_arr = np.asarray(y, int), np.asarray(prob, float)
    edges = np.linspace(0, 1, n_bins + 1)
    bins = []
    for lo, hi in itertools.pairwise(edges):
        mask = (p_arr >= lo) & ((p_arr < hi) if hi < 1 else (p_arr <= hi))
        if mask.any():
            bins.append(
                {
                    "lower": round(float(lo), 2),
                    "upper": round(float(hi), 2),
                    "count": int(mask.sum()),
                    "mean_predicted": round(float(p_arr[mask].mean()), 4),
                    "observed_rate": round(float(y_arr[mask].mean()), 4),
                }
            )
    return bins


def expected_calibration_error(y: ArrayLike, prob: ArrayLike, n_bins: int = 10) -> float:
    bins = calibration_bins(y, prob, n_bins)
    total = sum(b["count"] for b in bins)
    return round(
        sum(b["count"] / total * abs(b["mean_predicted"] - b["observed_rate"]) for b in bins), 4
    )


def classification_metrics(
    y: ArrayLike, prob: ArrayLike, threshold: float = 0.5
) -> dict[str, float | None]:
    y_arr, p_arr = np.asarray(y, int), np.asarray(prob, float)
    if y_arr.size == 0:
        raise ValueError("no samples")
    pred = (p_arr >= threshold).astype(int)
    both = len(set(y_arr.tolist())) == 2
    return {
        "pr_auc": _f(average_precision_score(y_arr, p_arr)) if both else None,
        "roc_auc": _f(roc_auc_score(y_arr, p_arr)) if both else None,
        "precision": _f(precision_score(y_arr, pred, zero_division=0)),
        "recall": _f(recall_score(y_arr, pred, zero_division=0)),
        "f1": _f(f1_score(y_arr, pred, zero_division=0)),
        "brier": _f(brier_score_loss(y_arr, p_arr)),
        "ece": expected_calibration_error(y_arr, p_arr),
        "positive_rate": _f(y_arr.mean()),
        "threshold": threshold,
    }
