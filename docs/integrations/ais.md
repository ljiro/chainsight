# AIS integration (AISStream, simulator, replay)

ChainSight ingests AIS through one provider-agnostic pipeline
(`services/ingestion/ais_pipeline.py`). Three providers are available, selected with
`AIS_PROVIDER`:

| Provider | `AIS_PROVIDER` | Data source key | Data mode | Needs credentials |
|---|---|---|---|---|
| AISStream live feed | `aisstream` | `aisstream` | real_time | `AISSTREAM_API_KEY` |
| Simulator | `simulator` (local default) | `ais-simulator` | **synthetic** | no |
| Replay of a recording | `replay` | `ais-replay` | historical | no |
| Off | `disabled` | — | — | — |

The data mode travels with every position (database → API → map), and the UI labels
synthetic and historical data explicitly.

## Using live AIS (AISStream)

1. Create a free account at <https://aisstream.io> and generate an API key.
2. In `.env`:
   ```env
   AIS_PROVIDER=aisstream
   AISSTREAM_API_KEY=your-key
   # Optional: limit to regions, as [[lat1, lon1], [lat2, lon2]] boxes (default: world)
   AIS_BOUNDING_BOXES=[[[48,-6],[62,10]],[[0,95],[10,110]]]
   # Optional: keep a raw recording for later replay
   AIS_RECORD_FILE=/data/ais-recording.ndjson
   ```
3. `docker compose up -d ais-consumer` (or `make ais` natively).
4. Check that it works: the **AISStream** row on the dashboard turns *Active / Fresh*, and
   `GET /api/v1/fleet/summary` reports `by_data_mode.real_time`.

The key is read only by the consumer process. It is never logged (including in
reconnect logs) and never sent to the browser.

### Behaviour and limits

- **Subscription:** sent immediately after connecting, with bounding boxes and the
  message types `PositionReport`, `StandardClassBPositionReport` and `ShipStaticData`.
  *Verify field names against the current AISStream docs if the provider changes them.*
- **Resilience:** exponential reconnect backoff (1 s → 60 s, with jitter) and an
  idle watchdog that reconnects a silent socket after 60 s.
- **Coverage:** AISStream aggregates terrestrial receivers. It is best-effort with no
  SLA, and open-ocean coverage is sparse. Gaps appear as broken track segments and
  never as interpolated lines.
- **Freshness:** the beat task marks the source *degraded* and raises an alert when no
  data arrives for more than 2 minutes (twice the 60 s expected interval).

## Simulator (synthetic)

`integrations/ais/simulator.py` emits AISStream-format messages for 24 vessels on 8
hand-drawn sea lanes between real UN/LOCODE port coordinates. Vessels sail, slow down,
and moor at both ends, which exercises port-call detection.

Safeguards so the data cannot be mistaken for real:

- MMSIs use MID **200**, which is not allocated to any flag state. Names start with
  `SYNTHETIC` and no IMO number is sent.
- Rows are stored under the `ais-simulator` source (`data_mode = synthetic`), and the UI
  shows a "Synthetic data" banner and badges.
- Positions are a deterministic function of wall-clock time, so restarts continue
  smoothly.

## Replay (historical)

`AIS_PROVIDER=replay` with `AIS_REPLAY_FILE` plays back an NDJSON file with one
AISStream message per line, such as one produced with `AIS_RECORD_FILE`. Original
timestamps are kept, so replayed vessels appear *stale* rather than live.
`AIS_REPLAY_SPEED` scales the pauses between messages (0 = as fast as possible).

## Pipeline guarantees

| Concern | How it is handled |
|---|---|
| Invalid messages | Rejected with a reason (`invalid_position`, `non_vessel_mmsi`, `future_timestamp`, …) and counted per 60 s window in `ingestion_logs.details` |
| AIS "not available" values | Heading 511, SOG 102.3, COG 360, ROT −128 → `NULL` |
| Duplicates | Dropped in-batch, by an LRU of recent keys, and by the `(vessel_id, observed_at, source_id)` primary key |
| Implausible movement | `quality_flags` bitmask (implied speed > 60 kn, out-of-order, missing SOG/COG, low accuracy); positions are kept, not dropped |
| Identity | Time-bounded MMSI/IMO/name identifiers. An MMSI moves to the vessel that owns its IMO; conflicting IMOs are refused and logged |
| Failure | A failed batch rolls back, the window is logged as `failed`/`partial`, and the source is marked `error` |
