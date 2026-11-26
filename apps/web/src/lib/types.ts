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
