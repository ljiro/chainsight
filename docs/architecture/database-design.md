# ChainSight — Database Design

Status: Draft v1 (Phase 0) · Engine: PostgreSQL 16 + PostGIS 3 + TimescaleDB 2

## 1. Conventions

| Topic | Convention |
|---|---|
| Primary keys | `id UUID` with default `gen_random_uuid()` (built into PG 13+). Hypertables use a composite key that includes the time column (TimescaleDB requirement). |
| Time | `TIMESTAMPTZ`, always UTC. Source time (`observed_at`, `event_at`) is stored separately from `ingested_at` (DB default `now()`). |
| Audit columns | `created_at`, `updated_at` on mutable entities. Append-only tables have only `ingested_at` or `created_at`. |
| Enums | `VARCHAR(32)` + named `CHECK` constraint (ADR-007). |
| Spatial | `geography(Point, 4326)` for points and `geography(LineString, 4326)` for routes, with GiST indexes. |
| Provenance | `source_id → data_sources.id`, plus `source_ref` (the provider's identifier). |
| Tenancy | Org-owned rows carry `organization_id`. Shared reference data (vessels, ports, carriers, AIS positions) has no org column. |
| Naming | snake_case, plural table names. Constraint names come from a SQLAlchemy naming convention (`pk_`, `fk_`, `uq_`, `ix_`, `ck_`). |
| Deletes | `ON DELETE CASCADE` only for pure child rows (memberships, logs). Everything else uses `RESTRICT`. |

## 2. Entity overview

```text
organizations ─┬─ organization_members ── users
               ├─ api_keys
               ├─ carrier_integrations ── carrier_integration_logs
               ├─ shipments ─┬─ containers
               │             ├─ shipment_items
               │             ├─ shipment_vessels ── vessels / voyages
               │             └─ shipment_events (PLANNED / ESTIMATED / ACTUAL)
               └─ research_experiments ── backtest_runs ── backtest_metrics

carriers ─┬─ carrier_integrations
          └─ vessel_carrier_history ── vessels ─┬─ vessel_positions (hypertable) ── vessel_position_sources
                                               └─ voyages ── voyage_port_calls ── ports ── port_observations (hypertable)

data_sources ── ingestion_logs;  referenced by every ingested fact
model_versions ─┬─ eta_predictions / delay_predictions / risk_assessments (append-only)
                └─ model_evaluations
research_datasets ── maritime_features (hypertable) ── research_signals
system_alerts, audit_logs
```

## 3. Tables by phase

### 3.1 Phase 1: foundation (migration `0001`, seeded by `0002`)

**organizations**: `id`, `name`, `slug UNIQUE`, timestamps.

**users**: `id`, `email` (unique index on `lower(email)`), `full_name`, `password_hash NULL` (NULL means SSO/invite pending), `is_active`, `is_superuser`, `last_login_at`, timestamps.

**organization_members**: `id`, `organization_id → organizations CASCADE`, `user_id → users CASCADE`, `role ∈ {owner, admin, analyst, viewer}`, `created_at`. `UNIQUE(organization_id, user_id)`.

**api_keys**: `id`, `organization_id`, `created_by_user_id NULL`, `name`, `key_prefix UNIQUE` (first 12 chars, shown in UI), `key_hash` (SHA-256 of the full key), `scopes TEXT[]`, `last_used_at`, `expires_at`, `revoked_at`, `created_at`. The plaintext key is shown once and never stored.

**data_sources**: the registry of every provider/feed.
`id`, `key UNIQUE` (e.g. `aisstream`), `name`, `category ∈ {ais, weather, port, carrier, trade, market, internal}`, `data_mode ∈ {real_time, periodic, historical, synthetic}`, `status ∈ {active, degraded, error, not_configured, disabled}`, `homepage_url`, `license_note`, `attribution`, `expected_update_interval_seconds`, `last_success_at`, `last_error_at`, `last_error_message`, timestamps.

**ingestion_logs**: one row per ingestion run or AIS consumer batch window.
`id`, `data_source_id → data_sources`, `started_at`, `finished_at`, `status ∈ {running, succeeded, partial, failed}`, `records_received/accepted/rejected/duplicate`, `error_message`, `details JSONB`. Index `(data_source_id, started_at DESC)`.

**carriers**: `id`, `code UNIQUE` (slug), `name`, `scac UNIQUE NULL`, `carrier_type ∈ {ocean_carrier, logistics_provider}`, `website_url`, timestamps.

**carrier_integrations**: the state of our connection to a carrier, per capability.
`id`, `carrier_id`, `organization_id NULL` (NULL = platform-level default), `capability ∈ {shipment_tracking, vessel_schedules, fleet_list}`, `status ∈ {connected, authentication_required, pending_approval, unavailable, demo_data_only}`, `adapter_key NULL`, `status_reason`, `config JSONB` (non-secret settings only), `last_checked_at`, `last_success_at`, timestamps.
`UNIQUE NULLS NOT DISTINCT (carrier_id, organization_id, capability)` (PG 15+).

**carrier_integration_logs**: `id`, `carrier_integration_id CASCADE`, `occurred_at`, `operation` (e.g. `track_shipment`), `outcome ∈ {success, failure, rate_limited, auth_error, timeout}`, `http_status`, `latency_ms`, `error_code`, `message`, `provider_request_id`. Index `(carrier_integration_id, occurred_at DESC)`. Request/response bodies are never logged in full because they may contain customer data.

**ports**: `id`, `unlocode UNIQUE NULL` (5 chars), `name`, `country_code CHAR(2)`, `location geography(Point,4326)`, `timezone`, `source_id → data_sources NULL`, `source_ref`, timestamps. GiST index on `location`. Port polygons/geofences for arrival detection come in Phase 2 (`port_areas`, geography polygon).

**system_alerts**: `id`, `organization_id NULL`, `severity ∈ {info, warning, critical}`, `category`, `title`, `message`, `entity_type`, `entity_id`, `data_source_id NULL`, `dedupe_key`, `raised_at`, `acknowledged_at`, `acknowledged_by → users`, `resolved_at`. Partial unique index on `dedupe_key WHERE resolved_at IS NULL` stops duplicate open alerts.

**audit_logs**: `id`, `organization_id NULL`, `actor_user_id NULL`, `actor_api_key_id NULL`, `action`, `entity_type`, `entity_id`, `occurred_at`, `ip_address INET`, `user_agent`, `changes JSONB`. Append-only.

### 3.2 Phase 2: fleet intelligence

**vessels** *(implemented in `0003`)*: `id`, `mmsi INT NULL`, `imo INT NULL UNIQUE`, `name`, `call_sign`, `ship_type_code SMALLINT` (AIS type), `vessel_class` + `vessel_class_source`. AIS ship types only distinguish cargo, tanker, passenger and so on, so `container`, `bulk`, `lng_carrier` and `lpg_carrier` are reserved for registry or manual enrichment and are never inferred from AIS. Also stores the latest position (`last_location`, `last_sog_knots`, … `last_position_source_id`), copied onto the row for the map, `length_m`, `beam_m`, `draught_m`, `destination` (as reported in AIS), `first_seen_at`, `last_seen_at`, timestamps. (Gross tonnage and flag state need a registry source; deferred.) MMSI is not a stable identity (it is reassigned on reflagging), so entity resolution prefers IMO and keeps `vessel_identifiers(vessel_id, scheme, value, valid_from, valid_to, source_id)` for history.

**vessel_carrier_history**: `id`, `vessel_id`, `carrier_id`, `relation ∈ {operator, owner, charterer, slot}`, `valid_from`, `valid_to NULL`, `source_id`, `confidence NUMERIC(3,2)`.

**vessel_positions** (hypertable on `observed_at`, 1-day chunks, compressed after 7 days, segment by `vessel_id`):
`vessel_id`, `observed_at`, `ingested_at`, `location geography(Point)`, `sog_knots`, `cog_deg`, `heading_deg`, `nav_status SMALLINT`, `rot`, `source_id`, `source_message_id`, `quality_flags SMALLINT` (bitmask).
`PRIMARY KEY (vessel_id, observed_at, source_id)`. This is the dedup key, and duplicates are dropped with `ON CONFLICT DO NOTHING`. Hypertables cannot be referenced by FKs, so related rows reference `(vessel_id, observed_at)` logically.

**vessel_position_sources**: per-receiver/provider metadata for a position batch (station, satellite vs. terrestrial, latency stats), keyed by `ingestion_log_id`.

**voyages**: `id`, `vessel_id`, `carrier_id NULL`, `voyage_number`, `service_code`, `origin_port_id`, `destination_port_id`, `departed_at`, `arrived_at`, `status ∈ {planned, underway, completed, cancelled}`, `source_id`.

**voyage_port_calls**: `id`, `voyage_id`, `port_id`, `sequence`, plus planned/estimated/actual `arrival_at`/`departure_at` columns. Detected calls (from geofence entry/exit) are marked with `detection_method ∈ {carrier_schedule, ais_geofence}`. `actual_arrival_at` is NULL when the vessel was already in port when first observed (the arrival time is unknown, not invented).

**maritime_routes**: `id`, `name`, `origin_port_id`, `destination_port_id`, `path geography(LineString)`, `distance_nm`, `source`. Used for remaining-distance features.

**port_areas**: `port_id`, `area geography(Polygon)`, `radius_m`, `method ∈ {circle_buffer, polygon}`. GiST index; used for port-call detection.

**weather_observations**: `port_id`, `location`, `kind ∈ {current, forecast, historical}`, `valid_at`, `retrieved_at`, temperature/wind/gust/precipitation, `source_id`. `UNIQUE(port_id, kind, valid_at, source_id)`, append-only.

**port_observations** (hypertable on `observed_at`): `port_id`, `observed_at`, `available_at`, `metric` (e.g. `vessels_in_port`, `arrivals`, `avg_wait_hours`, `portcalls_container`), `value`, `source_id`. PK `(port_id, metric, observed_at, source_id)`.

### 3.3 Phase 3: shipment intelligence

**shipments**: `id`, `organization_id`, `carrier_id`, `reference_type ∈ {container, booking, bill_of_lading}`, `reference_value`, `origin_port_id`, `destination_port_id`, `status` (derived current state), `planned_arrival_at` (the **original** plan, write-once), `source_id`, timestamps. `UNIQUE(organization_id, carrier_id, reference_type, reference_value)`.

**containers**: `id`, `organization_id`, `container_number` (ISO 6346, check digit validated), `iso_size_type`, `seal_number`. **shipment_items** links shipments ↔ containers with optional cargo description, weight, and HS code.

**shipment_vessels**: `id`, `shipment_id`, `vessel_id NULL`, `voyage_id NULL`, `leg_sequence`, `carrier_vessel_name`, `carrier_voyage_number`, `valid_from`, `source_id`. This records the carrier's claim about which vessel carries the cargo. It is never inferred from AIS.

**shipment_events** (append-only):
`id`, `shipment_id`, `container_id NULL`, `event_type ∈ {equipment, transport, shipment}`, `event_code` (DCSA: `LOAD`, `DISC`, `GTIN`, `GTOT`, `ARRI`, `DEPA`, …), `classifier ∈ {PLANNED, ESTIMATED, ACTUAL}`, `event_at`, `location_port_id`, `vessel_id NULL`, `source_id`, `source_event_id`, `raw_code` (the provider's original code), `ingested_at`.
`UNIQUE(source_id, source_event_id)` where present. A new ETA from a carrier is a new `ESTIMATED` row, and earlier rows remain.

### 3.4 Phase 4: machine learning

**model_versions**: `id`, `model_name` (e.g. `eta_lgbm`), `version`, `mlflow_run_id`, `training_data_snapshot` (dataset hash + time window), `training_cutoff_at`, `feature_schema JSONB`, `is_synthetic_trained BOOLEAN`, `status ∈ {candidate, production, archived}`, `created_at`.

**eta_predictions** (append-only): `id`, `vessel_id`, `voyage_id NULL`, `target_port_id`, `predicted_at`, `input_data_as_of`, `model_version_id`, `predicted_arrival_at`, `lower_bound_at`, `upper_bound_at`, `interval_level` (e.g. 0.8), `features_ref`, `actual_arrival_at NULL` (back-filled once known), `error_hours GENERATED`.

**delay_predictions** (append-only): `id`, `shipment_id`, `predicted_at`, `input_data_as_of`, `model_version_id`, `delay_probability`, `threshold_hours`, `calibration_method`, `actual_delayed NULL`, `actual_delay_hours NULL`.

**risk_assessments**: composite risk score per shipment or vessel with `factors JSONB` (explanations), `model_version_id`, `assessed_at`.

**model_evaluations**: `id`, `model_version_id`, `evaluated_at`, `split ∈ {validation, test, live}`, `window_start`, `window_end`, `metrics JSONB` (MAE, RMSE, PR-AUC, …), `n_samples`.

### 3.5 Phase 5: quantitative research

**research_datasets**: `id`, `name`, `description`, `frequency`, `definition JSONB` (the reproducible recipe), `content_hash`, `created_by`, `created_at`.

**maritime_features** (hypertable on `as_of`): `feature_key` (e.g. `arrivals.daily.CNSHA`), `as_of` (period end), `available_at` (first time it could have been known), `value DOUBLE`, `dataset_id`, `version`. PK `(feature_key, as_of, version)`. Research code reads with `available_at <= decision_time`.

**research_experiments**: `id`, `organization_id`, `name`, `hypothesis`, `config JSONB`, `status`, `created_by`, timestamps.

**research_signals**: `id`, `experiment_id`, `signal_key`, `as_of`, `available_at`, `value`.

**backtest_runs**: `id`, `experiment_id`, `started_at`, `finished_at`, `config JSONB` (universe, costs, slippage, sizing, walk-forward windows), `code_version` (git SHA), `data_snapshot_hash`, `status`.

**backtest_metrics**: `id`, `backtest_run_id`, `split ∈ {in_sample, out_of_sample}`, `metric` (sharpe, max_drawdown, turnover, …), `value`, plus a `benchmark_value` column.

## 4. Timescale policies (applied when the extension is present)

| Hypertable | Chunk | Compression | Retention | Continuous aggregate |
|---|---|---|---|---|
| vessel_positions | 1 day | after 7 days, segment by `vessel_id` | raw 180 days (configurable) | `vessel_positions_15m` (last position per vessel per 15 min) |
| port_observations | 30 days | after 90 days | none | — |
| maritime_features | 90 days | after 1 year | none | — |

When TimescaleDB is not installed (plain-PostGIS dev environments), migrations create normal tables with the same columns and indexes, and the readiness endpoint reports `timescaledb: unavailable`.

## 5. Migration policy

- Alembic, one linear history, files named `NNNN_<slug>.py`.
- Migrations are hand-reviewed and never import application models or enums, because they must stay valid as code evolves.
- CI runs `upgrade head → downgrade base → upgrade head` and a model/DB drift check (`tests/integration/test_migrations.py`).
- Reference data (carriers, data sources) is seeded in its own idempotent migration with `ON CONFLICT DO NOTHING`.
