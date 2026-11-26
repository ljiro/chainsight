"""Time-based validation. Random splits leak the future into training for time series, so
every split here is chronological, with an optional embargo gap between train and test."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta

import pandas as pd


class LeakageError(AssertionError):
    pass


@dataclass(frozen=True)
class TimeSplit:
    train: pd.DataFrame
    test: pd.DataFrame
    cutoff: pd.Timestamp


def time_split(
    df: pd.DataFrame, time_col: str, *, test_fraction: float = 0.25, gap: timedelta = timedelta(0)
) -> TimeSplit:
    """Train on the earliest (1 - test_fraction) of time, test on the rest. Rows inside the
    embargo `gap` after the cutoff are dropped so labels near the boundary cannot leak."""
    if not 0 < test_fraction < 1:
        raise ValueError("test_fraction must be in (0, 1)")
    if df.empty:
        raise ValueError("cannot split an empty dataset")
    ordered = df.sort_values(time_col)
    cutoff = ordered[time_col].quantile(1 - test_fraction)
    train = ordered[ordered[time_col] <= cutoff]
    test = ordered[ordered[time_col] > cutoff + gap]
    assert_no_leakage(train, test, time_col)
    return TimeSplit(train, test, pd.Timestamp(cutoff))


def walk_forward(
    df: pd.DataFrame, time_col: str, *, n_folds: int = 4, min_train_fraction: float = 0.4
) -> Iterator[TimeSplit]:
    """Expanding-window folds: each fold trains on everything before its test window."""
    ordered = df.sort_values(time_col).reset_index(drop=True)
    start = int(len(ordered) * min_train_fraction)
    fold = max(1, (len(ordered) - start) // n_folds)
    for i in range(n_folds):
        lo, hi = start + i * fold, start + (i + 1) * fold if i < n_folds - 1 else len(ordered)
        if lo >= len(ordered) or hi <= lo:
            break
        train, test = ordered.iloc[:lo], ordered.iloc[lo:hi]
        assert_no_leakage(train, test, time_col)
        yield TimeSplit(train, test, pd.Timestamp(train[time_col].max()))


def assert_no_leakage(train: pd.DataFrame, test: pd.DataFrame, time_col: str) -> None:
    if not train.empty and not test.empty and train[time_col].max() >= test[time_col].min():
        raise LeakageError(
            f"training data reaches {train[time_col].max()}, test starts {test[time_col].min()}"
        )
