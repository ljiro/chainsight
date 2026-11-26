# Model card: vessel ETA

**Question answered:** when will this vessel arrive at the port its AIS destination names?
This is *vessel* arrival. It says nothing about when a container is discharged or delivered.

## Models

| Version prefix | Algorithm | Interval |
|---|---|---|
| `baseline-distance-over-speed-v1` | Great-circle distance ÷ recent mean SOG (floor 1 kn) | ±20 % heuristic band, **not** a calibrated interval (`interval_level = NULL`) |
| `eta-qgbm-<timestamp>-<hash>` | Three `HistGradientBoostingRegressor` models with quantile loss (p10, p50, p90) | Nominal 80 % (p10–p90); empirical coverage measured on the test split and live |

Features (`ml/eta_prediction/features.py`): remaining great-circle distance, current and recent
mean SOG, bearing offset between course and target, baseline hours, length, tanker flag, hour
of day, day of week.

## Training data

Labels come from AIS port calls detected by ChainSight's geofences: for each completed call, the
vessel's earlier positions (with a destination resolving to that port) become rows labeled with
hours-to-arrival. Rows implying more than 40 kn are dropped as bad joins or AIS errors.

The trained model is promoted only if its test MAE beats the baseline on the same, later, test
period. Synthetic training (`--synthetic`) exists for development and is never promoted.

## Training domain and fallbacks

The training data only covers arrivals that happened while ChainSight was ingesting. With a few
hours of history, every label is a few hours long. The longest label and distance seen in
training are stored as `feature_schema.training_domain`. The trained model is used only inside
that domain (10 % margin). Otherwise the baseline is used and the prediction records why:

| `fallback_reason` | Meaning |
|---|---|
| `out_of_training_domain` | Remaining distance or baseline horizon is beyond what the model was trained on |
| `at_training_horizon_limit` | The model's upper bound reached its longest training label; labels are right-censored there, so the estimate is not meaningful |
| `model_artifact_unavailable` | The registered model file is missing on this worker |

## Evaluation (dev stack, 26 Nov 2026)

Time-based test split, 217 voyages, real AIS data:

| Model | MAE | Median AE | Bias | 80 % interval coverage |
|---|---|---|---|---|
| Quantile GBM | 0.91 h | 0.02 h | −0.19 h | 93 % |
| Baseline | 2.38 h | 1.21 h | +0.71 h | n/a (heuristic) |

These are **not** representative of long voyages. The test set inherits the same short-horizon
censoring as training (≤ 4 h, ≤ 88 nm), so the numbers describe final approaches only. Coverage
above 80 % suggests the interval is conservative on this sample.

## Limitations

- Short training domain until weeks of AIS history accumulate; most live predictions are the
  baseline. The UI shows which model produced each one.
- AIS destinations are free text entered by crews; only those that resolve to a known UN/LOCODE
  port are predicted, and a stale destination yields a confident wrong answer.
- Great-circle distance ignores land, canals and routing; long-haul baselines are optimistic.
- AISStream's terrestrial coverage is uneven (for example, very sparse around the Philippines),
  so gaps in input data are common. `input_data_as_of` shows how old the input was.
