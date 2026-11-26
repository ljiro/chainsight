from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ml.congestion_forecasting.pipeline import (
    PERSISTENCE_VERSION,
    SEASONAL_VERSION,
    WARMUP_H,
    seasonal_naive,
    usable_history,
)

T0 = datetime(2026, 11, 26, 6, tzinfo=UTC)


def test_warmup_hours_are_not_forecast_inputs() -> None:
    history = {T0 + timedelta(hours=h): float(v) for h, v in enumerate([20, 330, 390, 390, 385])}
    usable = usable_history(history, coverage_start=T0)
    assert min(usable) == T0 + timedelta(hours=WARMUP_H)
    assert usable_history(history, None) == history
    # Tomorrow 06:00 must not copy the cold-start value of 20.
    value, method = seasonal_naive(usable, T0 + timedelta(hours=24))
    assert (value, method) == (385.0, PERSISTENCE_VERSION)


def test_seasonal_naive_uses_same_hour_yesterday() -> None:
    history = {T0: 10.0, T0 + timedelta(hours=5): 12.0}
    assert seasonal_naive(history, T0 + timedelta(hours=24)) == (10.0, SEASONAL_VERSION)
    assert seasonal_naive({}, T0) == (None, PERSISTENCE_VERSION)
