from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

from ml.eta_prediction.features import FEATURES, baseline_hours, bearing_deg, build_features
from ml.eta_prediction.model import BaselineEtaModel, QuantileGbmEtaModel
from ml.eta_prediction.pipeline import ActiveEtaModel, drop_implausible, synthetic_frame


def test_baseline_hours_uses_recent_speed_with_floor() -> None:
    assert baseline_hours(120, 12, 20) == 10
    assert baseline_hours(120, 0.2, 10) == 12  # recent speed unusable: fall back to current
    assert baseline_hours(10, None, 0) == 10  # never divides by ~0


def test_features_point_toward_target() -> None:
    f = build_features(
        at=datetime(2026, 11, 26, 9, tzinfo=UTC),
        lat=0,
        lon=0,
        sog=12,
        cog=90,
        recent_mean_sog=12,
        target_lat=0,
        target_lon=1,
        length_m=300,
        vessel_class="tanker",
    )
    assert f.bearing_offset_deg == 0 and f.is_tanker == 1 and f.hour_utc == 9
    assert f.remaining_distance_nm == pytest.approx(60.0, rel=0.01)
    assert bearing_deg(0, 0, 1, 0) == pytest.approx(0)
    assert set(f.as_dict()) == set(FEATURES)


def test_quantile_model_orders_bounds_and_beats_nothing_on_synthetic() -> None:
    frame = synthetic_frame(120)
    model = QuantileGbmEtaModel(max_iter=60).fit(frame, frame["label_hours"])
    for est in model.predict(frame.head(20)):
        assert est.lower_hours <= est.hours <= est.upper_hours  # type: ignore[operator]
        assert est.interval_level == 0.8
    base = BaselineEtaModel().predict(frame.head(1))[0]
    assert base.interval_level is None and base.interval_method == "heuristic_pm20pct"


def test_domain_guard_falls_back_outside_training_range() -> None:
    gbm, base_id, model_id = object(), uuid.uuid4(), uuid.uuid4()
    active = ActiveEtaModel(
        model_id,
        gbm,
        "eta-qgbm-x",
        base_id,  # type: ignore[arg-type]
        {"max_label_hours": 4.0, "max_distance_nm": 60.0},
    )
    near = {"baseline_hours": 3.0, "remaining_distance_nm": 40.0}
    far = {"baseline_hours": 120.0, "remaining_distance_nm": 1500.0}
    assert active.choose(near)[0] == model_id and active.choose(near)[3] is None
    chosen = active.choose(far)
    assert chosen[0] == base_id and isinstance(chosen[1], BaselineEtaModel)
    assert chosen[3] == "out_of_training_domain"


def test_drop_implausible_rows() -> None:
    frame = pd.DataFrame({"remaining_distance_nm": [50.0, 4690.0], "label_hours": [5.0, 4.0]})
    kept, dropped = drop_implausible(frame)
    assert dropped == 1 and kept["remaining_distance_nm"].tolist() == [50.0]


def test_artifact_uri_is_relative_to_the_artifact_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from core.settings import get_settings
    from ml.common.registry import ArtifactUnavailable, ModelCard, load_artifact, save_artifact

    monkeypatch.setenv("MODEL_ARTIFACT_DIR", str(tmp_path))
    get_settings.cache_clear()
    try:
        card = ModelCard("eta", "eta-test-1", "test", False, {})
        uri = save_artifact({"w": 1}, card)
        assert uri == "eta/eta-test-1.joblib"  # portable across hosts and containers
        assert load_artifact(uri) == {"w": 1}
        with pytest.raises(ArtifactUnavailable):
            load_artifact("eta/missing.joblib")
    finally:
        get_settings.cache_clear()


def test_missing_artifact_serves_baseline_with_a_reason() -> None:
    base_id = uuid.uuid4()
    active = ActiveEtaModel(
        base_id,
        BaselineEtaModel(),
        "baseline",
        base_id,
        None,
        "model_artifact_unavailable",
    )
    chosen = active.choose({"baseline_hours": 3.0, "remaining_distance_nm": 40.0})
    assert chosen[0] == base_id and chosen[3] == "model_artifact_unavailable"


def test_saturated_trained_prediction_is_detected() -> None:
    from ml.eta_prediction.model import EtaEstimate

    base_id, model_id = uuid.uuid4(), uuid.uuid4()
    domain = {"max_label_hours": 4.0, "max_distance_nm": 60.0}
    active = ActiveEtaModel(model_id, object(), "q", base_id, domain)  # type: ignore[arg-type]
    assert active.saturated(EtaEstimate(4.0, 1.0, 4.0, 0.8, "quantile_gbm_p10_p90"))
    assert not active.saturated(EtaEstimate(2.0, 1.0, 3.0, 0.8, "quantile_gbm_p10_p90"))
    baseline_only = ActiveEtaModel(base_id, BaselineEtaModel(), "b", base_id, None)
    assert not baseline_only.saturated(EtaEstimate(40.0, 32.0, 48.0, None, "heuristic_pm20pct"))
