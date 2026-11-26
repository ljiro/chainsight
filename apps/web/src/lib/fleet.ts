/**
 * Pure fleet-map state helpers (no React, no MapLibre) so they can be unit tested.
 */
import type {
  FleetPositions,
  LivePositionMessage,
  PositionFreshness,
  VesselClass,
  VesselFeature,
} from "./types";

export const LIVE_S = 600;
export const RECENT_S = 3600;

export function freshnessFor(ageSeconds: number): PositionFreshness {
  return ageSeconds <= LIVE_S ? "live" : ageSeconds <= RECENT_S ? "recent" : "stale";
}

/**
 * Map color groups. The map is a scatter, so only three categorical slots are used
 * (validated all-pairs for CVD in both themes); every other class folds into "Other".
 * Colors are never the only encoding: the legend and vessel list carry labels.
 */
export type ClassGroup = "cargo" | "tanker" | "passenger" | "other";
export const CLASS_GROUPS: { key: ClassGroup; label: string }[] = [
  { key: "cargo", label: "Cargo" },
  { key: "tanker", label: "Tanker" },
  { key: "passenger", label: "Passenger" },
  { key: "other", label: "Other / unknown" },
];
export const GROUP_COLORS: Record<"light" | "dark", Record<ClassGroup, string>> = {
  light: { cargo: "#2a78d6", tanker: "#eb6834", passenger: "#1baf7a", other: "#898781" },
  dark: { cargo: "#3987e5", tanker: "#d95926", passenger: "#199e70", other: "#898781" },
};

export function classGroup(c: VesselClass): ClassGroup {
  if (c === "cargo" || c === "container" || c === "bulk") return "cargo";
  if (c === "tanker" || c === "lng_carrier" || c === "lpg_carrier") return "tanker";
  if (c === "passenger") return "passenger";
  return "other";
}

export interface FleetFilterState {
  query: string;
  groups: ClassGroup[]; // empty = all
  carrier: string | null;
  freshness: "all" | "live" | "recent";
}

export const DEFAULT_FILTERS: FleetFilterState = { query: "", groups: [], carrier: null, freshness: "all" };

export function matchesFilters(f: VesselFeature, filters: FleetFilterState): boolean {
  const p = f.properties;
  if (filters.groups.length && !filters.groups.includes(classGroup(p.vessel_class))) return false;
  if (filters.carrier && p.carrier !== filters.carrier) return false;
  if (filters.freshness === "live" && p.freshness !== "live") return false;
  if (filters.freshness === "recent" && p.freshness === "stale") return false;
  const q = filters.query.trim().toLowerCase();
  if (q && !(p.name ?? "").toLowerCase().includes(q) && String(p.mmsi ?? "") !== q) return false;
  return true;
}

export type VesselIndex = Map<string, VesselFeature>;

export function indexFeatures(collection: FleetPositions): VesselIndex {
  return new Map(collection.features.map((f) => [f.id, f]));
}

/**
 * Merge a live WebSocket message into the index. Only newer observations replace older
 * ones (messages can arrive out of order across reconnects); provenance comes from the
 * message, so live data is never relabelled.
 */
export function applyLiveUpdate(index: VesselIndex, msg: LivePositionMessage, now: Date = new Date()): VesselIndex {
  const next = new Map(index);
  for (const p of msg.positions) {
    const prev = next.get(p.vessel_id);
    if (prev && new Date(prev.properties.observed_at) >= new Date(p.observed_at)) continue;
    const age = Math.max(0, (now.getTime() - new Date(p.observed_at).getTime()) / 1000);
    next.set(p.vessel_id, {
      type: "Feature",
      id: p.vessel_id,
      geometry: { type: "Point", coordinates: [p.lon, p.lat] },
      properties: {
        id: p.vessel_id,
        mmsi: p.mmsi,
        name: p.name,
        vessel_class: p.vessel_class,
        carrier: prev?.properties.carrier ?? null,
        sog_knots: p.sog_knots,
        cog_deg: p.cog_deg,
        heading_deg: p.heading_deg,
        nav_status: p.nav_status,
        observed_at: p.observed_at,
        age_seconds: age,
        freshness: freshnessFor(age),
        source: msg.source,
        data_mode: msg.data_mode,
      },
    });
  }
  return next;
}

/** Recompute ages/freshness against the current clock (positions age while displayed). */
export function refreshAges(index: VesselIndex, now: Date = new Date()): VesselIndex {
  const next = new Map(index);
  for (const [id, f] of next) {
    const age = Math.max(0, (now.getTime() - new Date(f.properties.observed_at).getTime()) / 1000);
    next.set(id, { ...f, properties: { ...f.properties, age_seconds: age, freshness: freshnessFor(age) } });
  }
  return next;
}

export function dataModeCounts(features: VesselFeature[]): Record<string, number> {
  const out: Record<string, number> = {};
  for (const f of features) out[f.properties.data_mode] = (out[f.properties.data_mode] ?? 0) + 1;
  return out;
}

export function bboxParam(b: [number, number, number, number]): string {
  return b.map((v) => v.toFixed(4)).join(",");
}

/** Merge a REST snapshot into the index, keeping any newer live observation. */
export function mergeSnapshot(index: VesselIndex, collection: FleetPositions): VesselIndex {
  const next = indexFeatures(collection);
  for (const [id, live] of index) {
    const snap = next.get(id);
    if (snap && new Date(live.properties.observed_at) > new Date(snap.properties.observed_at)) {
      next.set(id, { ...live, properties: { ...live.properties, carrier: snap.properties.carrier } });
    }
  }
  return next;
}
