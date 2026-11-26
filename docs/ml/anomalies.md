# Model card: vessel anomaly detection

Rule-based, not learned (`ml/anomaly_detection/rules.py`). Runs every 5 minutes over the last
6 hours of positions for **watched vessels**: those with a current carrier association or on an
active shipment leg. Alerts go to `system_alerts` with `category = vessel_anomaly` and a stable
dedupe key per vessel and type. An alert resolves automatically once its condition clears.

| Type | Condition (defaults in `RuleConfig`) |
|---|---|
| `unexpected_stop` | SOG < 0.5 kn for ≥ 60 min, outside every port geofence, while reporting "under way" |
| `speed_drop` | Mean SOG over the last 15 min ≤ 40 % of the mean 15–45 min earlier, which was ≥ 10 kn; not in port |
| `ais_silence` | No position for 2–48 h after last moving at ≥ 3 kn outside port, **only if ChainSight's own feed was continuous** over that time |
| `long_port_wait` | In port longer than 3× the port's median call (minimum 24 h), or 72 h when no median exists; never raised when the arrival was not observed |
| `route_deviation` | Moving (> 5 kn) over the last hour, > 100 nm from the resolved destination, and that distance grew by ≥ 20 nm |

## Why the feed-continuity check matters

When ChainSight's AIS consumer was down, every watched vessel appeared to go silent. Silence
alerts now require that the feed had no gap over 10 minutes for the whole silent period, so a
ChainSight outage is not reported as vessels going dark. 34 such alerts in the dev database were
resolved when this check was added.

## Limitations

- Terrestrial AIS has coverage holes. "Silence" may mean no receiver nearby, and the alert text
  says so.
- Rules have fixed thresholds and no per-vessel or per-route baselines. Expect false positives at
  anchorages outside the 8 km port circles.
- Only watched vessels are checked, by design, to keep alerts actionable.
