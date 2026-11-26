# Predictive intelligence: model cards

ChainSight's Phase 4 models, what they are trained on, how they are evaluated, and where they
should not be trusted. Every number in the UI comes from these models, so these limits apply
to everything shown on `/predictions` and on the vessel and shipment pages.

| Model | Card | Type | Trained on | Status by default |
|---|---|---|---|---|
| Vessel ETA | [eta.md](eta.md) | Quantile gradient boosting (p10/p50/p90), physics baseline as fallback | Real AIS-observed arrivals | Trained model promoted only if it beats the baseline on a later time period |
| Shipment delay | [delay.md](delay.md) | Calibrated gradient boosting vs logistic baseline | Observed shipment arrivals (none yet) | **No model in production** until real arrivals exist |
| Vessel anomalies | [anomalies.md](anomalies.md) | Rules | Not trained | Always on, for vessels linked to a carrier or shipment |
| Port congestion | [congestion.md](congestion.md) | Seasonal naive / persistence | Not trained | Always on, for the 20 busiest observed ports |

## Shared guarantees

- **Append-only predictions.** `eta_predictions`, `delay_predictions` and `congestion_predictions`
  are protected by a database trigger (`prevent_prediction_rewrite`): only outcome columns
  (`actual_*`, `error_hours`, `resolved_at`) may be filled in later. A prediction is never
  revised in place; a newer one is a new row.
- **Every prediction records** the model version, `predicted_at`, `input_data_as_of`, and its
  uncertainty (interval bounds and how they were produced, or a probability with its
  calibration method).
- **Time-based evaluation only.** `ml/common/splits.py` splits by time and
  `assert_no_leakage` fails training if any test row precedes the end of training. Live
  outcomes are back-filled and evaluated again (`split = live`) by `ml.evaluate` every hour.
- **Point-in-time features.** Delay features use only events whose `available_at` is at or
  before the prediction time; ETA features use only positions at or before the anchor time.
- **Synthetic data is labeled.** `model_versions.is_synthetic_trained` is shown next to every
  output of such a model. A synthetic-trained model is never auto-promoted, and a synthetic
  delay model may only score synthetic shipments (enforced in `predict_live`).
- **Model registry.** `model_versions` holds every version (rule-based ones included) with its
  training window, dataset hash, feature schema and artifact URI. Artifact URIs are stored
  relative to `MODEL_ARTIFACT_DIR`; in Docker this is the shared `mldata` volume. MLflow
  (SQLite store) mirrors parameters and metrics for experiment comparison.

## Running

```sh
uv run python -m services.ml_jobs train-eta                 # needs >= ETA_MIN_TRAINING_VOYAGES arrivals
uv run python -m services.ml_jobs train-delay               # needs >= DELAY_MIN_TRAINING_SHIPMENTS arrivals
uv run python -m services.ml_jobs train-delay --synthetic --promote-synthetic   # demo only; labeled
uv run python -m services.ml_jobs predict | anomalies | congestion | evaluate

# In Docker, so the artifact lands on the shared volume:
docker compose exec worker python -m services.ml_jobs train-eta
```

Celery beat runs `ml.detect_anomalies` (5 min), `ml.predict` (30 min), `ml.congestion`
(hourly, :02) and `ml.evaluate` (hourly, :15).
