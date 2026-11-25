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
