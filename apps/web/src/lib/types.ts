// Mirrors the FastAPI response schemas (apps/api/chainsight_api/schemas).
// TODO(phase-6): generate from /api/v1/openapi.json instead of hand-maintaining.

export type ComponentState = "ok" | "degraded" | "error";

export interface ComponentHealth {
  name: string;
  status: ComponentState;
  latency_ms: number | null;
  detail: Record<string, unknown>;
}

export interface ReadinessResponse {
  status: ComponentState;
  time: string;
  components: ComponentHealth[];
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export type DataMode = "real_time" | "periodic" | "historical" | "synthetic";
export type DataSourceStatus = "active" | "degraded" | "error" | "not_configured" | "disabled";
export type Freshness = "fresh" | "stale" | "never" | "unknown";

export interface DataSource {
  key: string;
  name: string;
  category: "ais" | "weather" | "port" | "carrier" | "trade" | "market" | "internal";
  data_mode: DataMode;
  status: DataSourceStatus;
  homepage_url: string | null;
  license_note: string | null;
  attribution: string | null;
  expected_update_interval_seconds: number | null;
  last_success_at: string | null;
  last_error_at: string | null;
  freshness: Freshness;
}

export type IntegrationStatus =
  | "connected"
  | "authentication_required"
  | "pending_approval"
  | "unavailable"
  | "demo_data_only";

export interface CarrierIntegration {
  capability: "shipment_tracking" | "vessel_schedules" | "fleet_list";
  status: IntegrationStatus;
  status_reason: string | null;
  last_checked_at: string | null;
  last_success_at: string | null;
}

export interface Carrier {
  code: string;
  name: string;
  scac: string | null;
  carrier_type: "ocean_carrier" | "logistics_provider";
  website_url: string | null;
  integrations: CarrierIntegration[];
}

export interface ApiErrorBody {
  error: { code: string; message: string; details: unknown; request_id: string | null };
}

// ---- Fleet (Phase 2) ---------------------------------------------------------------------

export type VesselClass =
  | "cargo" | "tanker" | "passenger" | "fishing" | "tug_service" | "pleasure" | "high_speed"
  | "other" | "unknown" | "container" | "bulk" | "lng_carrier" | "lpg_carrier";

export type PositionFreshness = "live" | "recent" | "stale";

export interface Provenance {
  source: string | null;
  data_mode: DataMode | null;
}

export interface LatestPosition {
  lat: number;
  lon: number;
  sog_knots: number | null;
  cog_deg: number | null;
  heading_deg: number | null;
  nav_status: number | null;
  nav_status_label: string | null;
  observed_at: string;
  age_seconds: number;
  freshness: PositionFreshness;
  provenance: Provenance;
}

export interface CarrierAssociation {
  carrier_code: string;
  carrier_name: string;
  relation: string;
  method: "name_heuristic" | "manual" | "carrier_api";
  confidence: number;
  valid_from: string;
}

export interface VesselSummary {
  id: string;
  mmsi: number | null;
  imo: number | null;
  name: string | null;
  vessel_class: VesselClass;
  vessel_class_source: string | null;
  destination: string | null;
  carrier: CarrierAssociation | null;
  position: LatestPosition | null;
}

export interface PortRef {
  id: string;
  unlocode: string | null;
  name: string;
  country_code: string;
}

export interface PortCall {
  id: string;
  port: PortRef;
  vessel_id: string;
  vessel_name: string | null;
  actual_arrival_at: string | null;
  actual_departure_at: string | null;
  duration_seconds: number | null;
  detection_method: string;
  source: string | null;
}

export interface VesselDetail extends VesselSummary {
  call_sign: string | null;
  ship_type_code: number | null;
  length_m: number | null;
  beam_m: number | null;
  draught_m: number | null;
  first_seen_at: string | null;
  identifiers: { scheme: string; value: string; valid_from: string; valid_to: string | null; source: string | null }[];
  carrier_history: CarrierAssociation[];
  open_port_call: PortCall | null;
}

export interface TrackResponse {
  vessel_id: string;
  start: string;
  end: string;
  total_points: number;
  returned_points: number;
  geometry: { type: "MultiLineString"; coordinates: [number, number][][] };
  points: { t: string; lat: number; lon: number; sog_knots: number | null; cog_deg: number | null; quality_flags: number }[];
  gaps: { start: string; end: string; duration_seconds: number }[];
  gap_threshold_seconds: number;
  sources: Provenance[];
}

export interface VesselFeatureProps {
  id: string;
  mmsi: number | null;
  name: string | null;
  vessel_class: VesselClass;
  carrier: string | null;
  sog_knots: number | null;
  cog_deg: number | null;
  heading_deg: number | null;
  nav_status: number | null;
  observed_at: string;
  age_seconds: number;
  freshness: PositionFreshness;
  source: string | null;
  data_mode: DataMode | "unknown";
}

export interface VesselFeature {
  type: "Feature";
  id: string;
  geometry: { type: "Point"; coordinates: [number, number] };
  properties: VesselFeatureProps;
}

export interface FleetPositions {
  type: "FeatureCollection";
  features: VesselFeature[];
  meta: { generated_at: string; count: number; truncated: boolean; by_data_mode: Record<string, number>; seen_within_s: number };
}

export interface FleetSummary {
  generated_at: string;
  vessels_total: number;
  vessels_live: number;
  vessels_recent: number;
  underway: number;
  at_anchor_or_moored: number;
  in_port: number;
  by_class: Record<string, number>;
  by_data_mode: Record<string, number>;
  carrier_associations: Record<string, number>;
  port_calls_last_24h: number;
}

export interface Port {
  id: string;
  unlocode: string | null;
  name: string;
  country_code: string;
  lat: number;
  lon: number;
  geofence_radius_m: number | null;
  vessels_in_port: number;
}

export interface ApproachingVessel {
  vessel: VesselSummary;
  port: PortRef;
  distance_nm: number;
}

export interface Alert {
  id: string;
  severity: "info" | "warning" | "critical";
  category: string;
  title: string;
  message: string | null;
  data_source: string | null;
  raised_at: string;
  resolved_at: string | null;
}

export interface LivePositionMessage {
  type: "positions";
  source: string;
  data_mode: DataMode;
  positions: {
    vessel_id: string;
    mmsi: number | null;
    name: string | null;
    vessel_class: VesselClass;
    lat: number;
    lon: number;
    sog_knots: number | null;
    cog_deg: number | null;
    heading_deg: number | null;
    nav_status: number | null;
    observed_at: string;
  }[];
}

// ---- Shipments (Phase 3) -----------------------------------------------------------------

export type ShipmentStatus =
  | "unknown" | "planned" | "at_origin" | "loaded" | "in_transit" | "transshipment" | "discharged" | "delivered";
export type DelayStatus = "unknown" | "on_time" | "delayed" | "arrived_on_time" | "arrived_late";
export type Classifier = "PLANNED" | "ESTIMATED" | "ACTUAL";

export interface ShipmentLocation {
  unlocode: string | null;
  port_name: string | null;
  mapped: boolean;
}

export interface ShipmentSummary {
  id: string;
  carrier: { code: string; name: string };
  reference_type: "container" | "booking" | "bill_of_lading";
  reference_value: string;
  origin: ShipmentLocation;
  destination: ShipmentLocation;
  status: ShipmentStatus;
  planned_arrival_at: string | null;
  current_eta_at: string | null;
  actual_arrival_at: string | null;
  delay_hours: number | null;
  delay_status: DelayStatus;
  last_event_at: string | null;
  last_refreshed_at: string | null;
  containers: string[];
  provenance: { source: string; data_mode: DataMode };
}

export interface ShipmentEvent {
  id: string;
  event_type: "equipment" | "transport" | "shipment";
  event_code: string;
  classifier: Classifier;
  event_at: string;
  event_created_at: string | null;
  location_unlocode: string | null;
  location_name: string | null;
  container_number: string | null;
  vessel_name: string | null;
  vessel_imo: number | null;
  voyage_number: string | null;
  description: string | null;
  raw_code: string | null;
  ingested_at: string;
}

export interface VesselLeg {
  sequence: number;
  carrier_vessel_name: string | null;
  carrier_vessel_imo: number | null;
  carrier_voyage_number: string | null;
  load_unlocode: string | null;
  discharge_unlocode: string | null;
  vessel_id: string | null;
  vessel_position: LatestPosition | null;
}

export interface ShipmentDetail extends ShipmentSummary {
  events: ShipmentEvent[];
  legs: VesselLeg[];
  integration_status: IntegrationStatus | null;
  refreshable: boolean;
}

export interface ShipmentsSummary {
  total: number;
  active: number;
  delayed: number;
  arrived_late: number;
  by_status: Record<string, number>;
  by_delay_status: Record<string, number>;
  by_data_mode: Record<string, number>;
}

export interface ImportReport {
  ok: boolean;
  rows: number;
  shipments_created: number;
  shipments_updated: number;
  events_inserted: number;
  events_duplicate: number;
  errors: { row: number; field: string; message: string }[];
}

export interface CarrierPerformance {
  code: string;
  name: string;
  carrier_type: string;
  integration_status: IntegrationStatus | null;
  integration_reason: string | null;
  last_call_at: string | null;
  last_call_outcome: string | null;
  vessels_associated: number;
  shipments_tracked: number;
  shipments_active: number;
  shipments_delayed: number;
  arrivals: number;
  on_time_rate: number | null;
  avg_arrival_delay_hours: number | null;
}

// ---- Predictive intelligence (Phase 4) -------------------------------------------------

export type ModelEvaluation = {
  split: string;
  metrics: Record<string, unknown>;
  n_samples: number | null;
  data_mode: string | null;
  evaluated_at: string;
};

export type ModelVersion = {
  id: string;
  model_name: "eta" | "delay" | "congestion" | string;
  version: string;
  algorithm: string;
  status: "production" | "candidate" | "retired" | string;
  is_synthetic_trained: boolean;
  n_train: number | null;
  n_test: number | null;
  training_window_start: string | null;
  training_window_end: string | null;
  feature_schema: Record<string, unknown> | null;
  notes: string | null;
  created_at: string;
  evaluations: ModelEvaluation[];
  predictions: number;
};

type ModelRef = { model_version: string; model_algorithm: string; is_synthetic_trained: boolean };

export type EtaPrediction = ModelRef & {
  id: string;
  vessel_id: string;
  vessel_name: string | null;
  mmsi: number | null;
  target_unlocode: string | null;
  target_port_name: string | null;
  predicted_at: string;
  input_data_as_of: string;
  predicted_arrival_at: string;
  lower_bound_at: string | null;
  upper_bound_at: string | null;
  interval_level: number | null;
  remaining_distance_nm: number | null;
  interval_method: string | null;
  fallback_reason: string | null;
  actual_arrival_at: string | null;
  error_hours: number | null;
};

export type RiskFactor = { factor: string; text: string; value_hours?: number };

export type DelayPrediction = ModelRef & {
  id: string;
  shipment_id: string;
  reference_value: string;
  carrier_name: string;
  destination_unlocode: string | null;
  predicted_at: string;
  input_data_as_of: string;
  delay_probability: number;
  threshold_hours: number;
  calibration_method: string | null;
  actual_delayed: boolean | null;
  actual_delay_hours: number | null;
  shipment_data_mode: string;
  factors: RiskFactor[];
};

export type ForecastPoint = { target_time: string; value: number; lower: number | null; upper: number | null };

export type CongestionPort = {
  port_id: string;
  unlocode: string;
  name: string;
  current_value: number | null;
  observed_at: string | null;
  history_hours: number;
  forecast: ForecastPoint[];
  method: string | null;
  predicted_at: string;
};

export type PortCongestion = {
  port_id: string;
  observations: Record<string, { t: string; value: number }[]>;
  forecast: ForecastPoint[];
  method: string | null;
};

export type VesselAnomaly = {
  id: string;
  vessel_id: string;
  vessel_name: string | null;
  severity: "info" | "warning" | "critical" | string;
  title: string;
  message: string;
  type: string;
  evidence: Record<string, unknown>;
  raised_at: string;
  resolved_at: string | null;
};

export type PredictionsSummary = {
  eta_vessels: number;
  high_risk: number;
  delay_model_synthetic: boolean | null;
  open_anomalies: number;
  congestion_ports: number;
  eta_resolved: number;
};

// ---- Quantitative research (Phase 5) ---------------------------------------------------

export type ResearchIndicator = {
  key: string;
  name: string;
  category: "maritime" | "market";
  kind: "series" | "composite";
  entity_kind?: string;
  entity_name?: string;
  metric?: string;
  unit?: string | null;
  publication_lag_days?: number;
  attributes?: { availability_rule?: Record<string, unknown>; original_source?: string; estimated?: boolean } & Record<string, unknown>;
  source_key?: string;
  source_name?: string;
  data_mode?: string;
  first_as_of?: string | null;
  last_as_of?: string | null;
  n_obs?: number;
  n_revisions?: number;
  description?: string;
  inputs?: string[];
  available?: boolean;
};

export type IndicatorCatalog = {
  indicators: ResearchIndicator[];
  ais_metrics: Record<string, string>;
  transforms: string[];
  frequencies: Record<string, string>;
};

export type SeriesPoint = { as_of: string; value: number | null; available_at: string };

export type FeatureSeries = {
  key: string;
  name: string;
  frequency: string;
  transform: string;
  n: number;
  median_availability_lag_days: number;
  points: SeriesPoint[];
};

export type MarketSeries = { key: string; name: string; unit: string | null; n: number; points: SeriesPoint[] };

export type Section<T> = { ok: true; result: T } | { ok: false; reason: string };

export type Stationarity = {
  n: number;
  adf_stat: number | null;
  adf_p: number | null;
  kpss_stat: number | null;
  kpss_p: number | null;
  verdict: "stationary" | "non_stationary" | "inconclusive";
};

export type Analysis = {
  feature: string;
  asset: string;
  alignment: { note: string; n: number; first: string; last: string; median_staleness_days: number; execution_lag_days: number };
  correlation: Section<{ n: number; pearson: number | null; pearson_p: number | null; spearman: number | null; spearman_p: number | null }>;
  lagged_correlation: Section<{ lag: number; corr: number | null; p: number | null; n: number; ci95: number | null }[]>;
  rolling_correlation: Section<{ t: string; value: number | null }[]>;
  stationarity: { feature: Section<Stationarity>; returns: Section<Stationarity> };
  regression: Section<{ n: number; alpha: number | null; beta: number | null; beta_t: number | null; beta_p: number | null; r2: number | null; hac_lags: number }>;
  granger: Section<{ lag: number; f: number | null; p: number | null }[]>;
  out_of_sample: Section<{ n_test: number; r2_oos: number | null; hit_rate: number | null; hit_rate_mean_forecast: number | null; min_train: number }>;
  scatter: { x: number; y: number; t: string }[];
  disclaimer: string;
};

export type MetricCell = { value: number | null; benchmark: number | null };

export type BacktestPoint = {
  t: string;
  position: number;
  signal: number | null;
  net_return: number;
  equity: number;
  benchmark_equity: number;
  drawdown: number;
};

export type BacktestFold = {
  train_start: string;
  train_end: string;
  test_start: string;
  test_end: string;
  lookback: number;
  threshold: number;
  direction: number;
  train_sharpe: number | null;
};

export type ExperimentConfig = {
  feature: string;
  asset: string;
  execution_lag_days?: number;
  backtest?: Record<string, unknown>;
  resolved_backtest?: Record<string, unknown>;
};

export type BacktestRunSummary = {
  id: string;
  status: "running" | "succeeded" | "failed";
  started_at: string;
  finished_at: string | null;
  error: string | null;
  code_version: string | null;
  data_snapshot_hash: string | null;
  metrics: Record<string, MetricCell> | null;
};

export type BacktestRun = BacktestRunSummary & {
  config: ExperimentConfig;
  series: { points?: BacktestPoint[]; in_sample_params?: Record<string, number>; n_configs_tested?: number };
  folds: BacktestFold[];
};

export type Experiment = {
  id: string;
  name: string;
  hypothesis: string;
  config: ExperimentConfig;
  status: "draft" | "completed" | "failed";
  created_at: string;
  updated_at: string;
  n_runs?: number;
  latest_run?: BacktestRunSummary | null;
};

export type ExperimentDetail = Experiment & { runs: BacktestRun[]; disclaimer: string };

export type DeflatedSharpe = {
  experiment_id: string;
  name: string;
  run_id: string;
  n_periods: number | null;
  sharpe_per_period: number | null;
  skew: number | null;
  kurtosis: number | null;
  moments_assumed_normal: boolean;
  sr_benchmark: number | null;
  deflated_sharpe: number | null;
  psr_zero: number | null;
  p_value: number | null;
  p_value_bh: number | null;
};

export type MultipleTesting = {
  n_experiments: number;
  n_trials: number;
  trial_sharpe_variance: number | null;
  experiments: DeflatedSharpe[];
  method: string;
  disclaimer: string;
};

export type Role = "owner" | "admin" | "analyst" | "viewer";

export type Me = {
  user: { id: string; email: string; full_name: string | null; last_login_at: string | null } | null;
  organization: { id: string; name: string; slug: string };
  role: Role | null;
  permissions: string[];
  auth_method: "session" | "api_key";
};

export type Organization = { id: string; name: string; slug: string; created_at: string; member_count: number };

export type Member = {
  user_id: string;
  email: string;
  full_name: string | null;
  is_active: boolean;
  last_login_at: string | null;
  role: Role;
  joined_at: string;
};

export type ApiKeyInfo = {
  id: string;
  name: string;
  key_prefix: string;
  scopes: string[];
  created_at: string;
  last_used_at: string | null;
  expires_at: string | null;
  revoked_at: string | null;
  created_by: string | null;
};

export type CreatedApiKey = ApiKeyInfo & { key: string; role: Role };

export type AuditEntry = {
  id: string;
  occurred_at: string;
  action: string;
  entity_type: string | null;
  entity_id: string | null;
  ip_address: string | null;
  changes: Record<string, unknown>;
  actor_email: string | null;
  actor_api_key: string | null;
};
