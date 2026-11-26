# Model card: shipment delay probability

**Question answered:** what is the probability that this shipment arrives at its destination
more than `SHIPMENT_DELAY_THRESHOLD_HOURS` (default 12 h) after the *original* planned arrival?

## Models

| Model | Algorithm |
|---|---|
| Baseline | Standardized logistic regression, `class_weight="balanced"` |
| Candidate | `HistGradientBoostingClassifier`, `class_weight="balanced"`, wrapped in isotonic `CalibratedClassifierCV` |

The candidate is chosen unless the baseline has a strictly higher PR-AUC on the later test
period. Both versions are registered and evaluated either way.

Features (`ml/delay_prediction/features.py`), built only from carrier events whose
`available_at` ≤ prediction time: planned transit days, progress, days since first event,
vessels and transshipment, latest estimate vs original plan, re-plan delta, departure delay,
departed flag, count of ACTUAL events. The "contributing signals" shown in the UI
(`risk_factors`) are readable restatements of these features, not model internals.

## Training data: none real yet

A model needs shipments with an observed arrival. ChainSight has no real carrier integration
yet (no credentials; see `docs/integrations/carriers.md`), so **training on real data refuses
to run** (`InsufficientData`) and no delay model is in production. The dashboard's high-risk KPI
stays empty and says so.

For demos, `train-delay --synthetic` replays demo-carrier journeys polled every 12 h, so each
event becomes "known" only when a poll would have seen it. Such a model:

- has `is_synthetic_trained = true`, shown as **Synthetic-trained** wherever its output appears;
- is promoted only with the explicit `--promote-synthetic` flag;
- may score **only synthetic shipments**. Real or historical shipments are never scored by it.

## Metrics

PR-AUC, ROC-AUC, precision, recall, F1 at 0.5, Brier score, expected calibration error and a
10-bin calibration table, on a time-based split. Metrics on synthetic journeys measure whether
the code works, not how carriers behave.

## Risk levels

`risk_assessments.level`: high ≥ 0.7 (`DELAY_ALERT_PROBABILITY`), medium ≥ 0.4, else low.

## Limitations

- Carrier event quality dominates: a carrier that never re-issues estimates leaves the model
  with only planned dates.
- The DCSA adapter is not verified against a live carrier.
- Small samples from one workspace do not represent a carrier's reliability.
