# Model card: port congestion forecast

**Quantity:** vessels in port (count of open AIS port calls) per hour, for the 20 ports with
the highest observed values in the last 48 hours. Forecast horizon: 24 hourly steps.

## Observations

`build_observations` writes hourly `port_observations` (`vessels_in_port`, `arrivals`) for
completed hours, counting only port calls ChainSight had recorded by that hour. Calls detected
late are never backdated.

The first **3 hours after tracking began** (`WARMUP_H`) under-count, because vessels already in
port are only detected as their positions arrive. Those observations are kept and shown, but
they are never used as forecast inputs. Without this, the seasonal-naive rule copied the
cold-start ramp into the next day.

## Methods

| Version | Rule |
|---|---|
| `seasonal-naive-24h-v1` | Value at the same hour yesterday |
| `persistence-v1` | Latest usable value, used for hours where yesterday's value is missing |

A forecast run can mix both. The API lists every method used in a run. Intervals are added only
once a port has ≥ 24 scored past errors: ± the 80th percentile of absolute error. Before that,
no interval is shown.

## Evaluation

`ml.evaluate` back-fills actual values and records live MAE (in vessels) per method. Gradient
boosting on lags (roadmap) needs weeks of hourly history first.

## Limitations

- Counts reflect AIS coverage and every vessel type, including pleasure craft and harbour
  vessels inside the 8 km circle. They are not official port statistics.
- The circle geofences come from UN/LOCODE points. Neighbouring entries (for example Amsterdam
  and Duivendrecht) overlap and count the same vessels.
- With less than a day of history, the forecast is mostly persistence.
