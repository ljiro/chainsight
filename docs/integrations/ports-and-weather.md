# Port registry (UN/LOCODE) and weather (Open-Meteo)

## Port registry

- **Source:** the UN/LOCODE code list (maintained by UNECE), via the CSV mirror at
  <https://github.com/datasets/un-locode> (`UN_LOCODE_URL` to override).
- **Licensing:** the mirror's own files are ODC-PDDL. The licence of the UN/LOCODE data
  itself is not stated there, so ChainSight **downloads it at import time and never
  commits it** to the repository.
- **Selection:** locations whose function code starts with `1` (port) and that have
  coordinates. Entries marked `X` (scheduled for removal) are skipped. That is about
  11,800 ports.
- **Precision:** coordinates are given to the minute (about 1.85 km), and sometimes mark
  the city rather than the terminal.
- **Coverage gaps:** about a third of UN/LOCODE port entries have **no coordinates** and
  cannot be geofenced. In the Philippines it is 271 of 377 (72%), including the Manila port
  codes (PHMNL, PHMNN North Harbour, PHMNS South Harbour). Port-call detection there is
  therefore incomplete until a registry with coordinates (e.g. the NGA World Port Index)
  is loaded.
- **Exclusions:** `EXCLUDED_CODES` in `integrations/ports/unlocode.py` lists entries that
  carry the port flag but are not ports. Each entry gives its reason. The first is
  `PHPIS` ("Philippines", a country-level placeholder in central Manila).
- **Pruning:** `--prune` removes ports that are no longer in the registry. Use it only
  with a complete registry file. Ports that are still referenced (calls, voyages,
  weather) are kept and reported.
- **Geofences:** a circular buffer around each port (`PORT_GEOFENCE_RADIUS_M`, default
  8 km), stored in `port_areas` with `method = circle_buffer`. Curated polygons can be
  added later with `method = polygon`.

```bash
make ports                                    # native
docker compose run --rm migrate               # Compose: imports only if the registry is empty
uv run python -m services.ingestion.port_registry --file code-list.csv --radius-m 6000
```

The NGA World Port Index (public domain) has much better coordinate coverage, but its
download endpoint refuses automated requests, and ChainSight does not work around
that. To use it, download the CSV manually from <https://msi.nga.mil> (Publications →
World Port Index) in a browser. An importer for that file is planned.

### Port-call detection

`services/fleet_tracking/detection.py` is a state machine over each vessel's
time-ordered fixes:

- **Arrival:** inside a geofence and stopped (SOG ≤ 0.5 kn, or at anchor / moored).
  Passing through at speed is a transit, not a call.
- **Departure:** outside the call's geofence and moving (> 1 kn). A stationary fix that
  jitters just over the edge does not end the call.
- **Overlapping geofences:** the nearest port wins.
- **Vessel first seen already in port:** the arrival time is recorded as **unknown**
  (`actual_arrival_at = NULL`, shown as "Before tracking began") rather than set to the
  first observation time.

A departure opens an `underway` voyage, and the next arrival completes it.

## Weather

- **Provider:** Open-Meteo forecast API, `current=` variables (temperature, precipitation,
  wind speed, direction and gusts) in knots and UTC. Many coordinates are fetched per
  request.
- **Terms:** data is CC BY 4.0 ("Weather data by Open-Meteo.com"). The free API is for
  non-commercial use; set `OPEN_METEO_API_KEY` and `OPEN_METEO_BASE_URL` for the
  commercial tier.
- **Schedule:** hourly (beat `port-weather`, minute 5) for `WEATHER_PORT_UNLOCODES` plus
  ports with an AIS port call in the last 7 days, capped at `WEATHER_MAX_PORTS`.
- **History:** observations are append-only (`ON CONFLICT DO NOTHING` on
  `(port, kind, valid_at, source)`), which builds a timestamped history for Phase 4
  features.
- **Failure:** retries on 429/5xx (honoring `Retry-After`); 4xx errors are not retried.
  On failure the run is logged as `failed`, the source is marked `error`, and the worker
  carries on.

Trigger a run manually:
`docker compose exec worker celery -A services.worker.app call ingest.weather.ports`.
