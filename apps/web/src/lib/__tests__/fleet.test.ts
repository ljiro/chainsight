import { describe, expect, it } from "vitest";
import {
  DEFAULT_FILTERS,
  applyLiveUpdate,
  classGroup,
  dataModeCounts,
  freshnessFor,
  indexFeatures,
  matchesFilters,
  mergeSnapshot,
  refreshAges,
} from "@/lib/fleet";
import { reconnectDelay } from "@/lib/live";
import type { FleetPositions, LivePositionMessage, VesselFeature } from "@/lib/types";

const NOW = new Date("2026-11-26T12:00:00Z");

function feature(id: string, overrides: Partial<VesselFeature["properties"]> = {}, coords: [number, number] = [4, 52]): VesselFeature {
  return {
    type: "Feature",
    id,
    geometry: { type: "Point", coordinates: coords },
    properties: {
      id, mmsi: 200000001, name: `SHIP ${id}`, vessel_class: "cargo", carrier: null, sog_knots: 12,
      cog_deg: 90, heading_deg: 90, nav_status: 0, observed_at: "2026-11-26T11:58:00Z", age_seconds: 120,
      freshness: "live", source: "aisstream", data_mode: "real_time", ...overrides,
    },
  };
}

const collection = (features: VesselFeature[]): FleetPositions => ({
  type: "FeatureCollection",
  features,
  meta: { generated_at: NOW.toISOString(), count: features.length, truncated: false, by_data_mode: {}, seen_within_s: 86400 },
});

const live = (id: string, observedAt: string, lon = 5): LivePositionMessage => ({
  type: "positions",
  source: "ais-simulator",
  data_mode: "synthetic",
  positions: [{ vessel_id: id, mmsi: 1, name: "LIVE", vessel_class: "tanker", lat: 50, lon, sog_knots: 9,
                cog_deg: 10, heading_deg: 10, nav_status: 0, observed_at: observedAt }],
});

describe("freshness and classes", () => {
  it.each([[0, "live"], [600, "live"], [601, "recent"], [3601, "stale"]] as const)("%i s -> %s", (age, f) => {
    expect(freshnessFor(age)).toBe(f);
  });
  it("folds classes beyond the three map colors into Other", () => {
    expect(classGroup("container")).toBe("cargo");
    expect(classGroup("lng_carrier")).toBe("tanker");
    expect(classGroup("fishing")).toBe("other");
    expect(classGroup("unknown")).toBe("other");
  });
});

describe("applyLiveUpdate", () => {
  it("adds new vessels with the message's provenance", () => {
    const next = applyLiveUpdate(new Map(), live("v1", "2026-11-26T11:59:50Z"), NOW);
    const f = next.get("v1")!;
    expect(f.properties.data_mode).toBe("synthetic");
    expect(f.properties.source).toBe("ais-simulator");
    expect(f.properties.freshness).toBe("live");
    expect(f.geometry.coordinates).toEqual([5, 50]);
  });

  it("ignores out-of-order (older) updates", () => {
    const idx = indexFeatures(collection([feature("v1", { observed_at: "2026-11-26T11:59:00Z" })]));
    const next = applyLiveUpdate(idx, live("v1", "2026-11-26T11:58:00Z"), NOW);
    expect(next.get("v1")!.properties.observed_at).toBe("2026-11-26T11:59:00Z");
  });

  it("keeps the known carrier when a live update replaces a vessel", () => {
    const idx = indexFeatures(collection([feature("v1", { carrier: "msc" })]));
    const next = applyLiveUpdate(idx, live("v1", "2026-11-26T11:59:59Z"), NOW);
    expect(next.get("v1")!.properties.carrier).toBe("msc");
  });

  it("does not mutate the previous index", () => {
    const idx = new Map();
    applyLiveUpdate(idx, live("v1", "2026-11-26T11:59:59Z"), NOW);
    expect(idx.size).toBe(0);
  });
});

describe("mergeSnapshot", () => {
  it("keeps newer live positions over an older REST snapshot", () => {
    const withLive = applyLiveUpdate(new Map(), live("v1", "2026-11-26T11:59:59Z", 9), NOW);
    const merged = mergeSnapshot(withLive, collection([feature("v1", { observed_at: "2026-11-26T11:50:00Z", carrier: "one" })]));
    expect(merged.get("v1")!.geometry.coordinates).toEqual([9, 50]);
    expect(merged.get("v1")!.properties.carrier).toBe("one");
  });
  it("drops vessels no longer in the snapshot window", () => {
    const merged = mergeSnapshot(indexFeatures(collection([feature("gone")])), collection([feature("v2")]));
    expect([...merged.keys()]).toEqual(["v2"]);
  });
});

describe("refreshAges", () => {
  it("ages positions so they cannot stay live forever", () => {
    const idx = indexFeatures(collection([feature("v1", { observed_at: "2026-11-26T10:00:00Z" })]));
    expect(refreshAges(idx, NOW).get("v1")!.properties.freshness).toBe("stale");
  });
});

describe("matchesFilters", () => {
  const cargo = feature("a", { name: "EVER ALPHA", carrier: "evergreen" });
  const tanker = feature("b", { vessel_class: "tanker", freshness: "stale", mmsi: 211000999 });
  it("filters by group, carrier, freshness and query", () => {
    expect(matchesFilters(cargo, DEFAULT_FILTERS)).toBe(true);
    expect(matchesFilters(tanker, { ...DEFAULT_FILTERS, groups: ["cargo"] })).toBe(false);
    expect(matchesFilters(cargo, { ...DEFAULT_FILTERS, carrier: "msc" })).toBe(false);
    expect(matchesFilters(tanker, { ...DEFAULT_FILTERS, freshness: "recent" })).toBe(false);
    expect(matchesFilters(cargo, { ...DEFAULT_FILTERS, query: "alpha" })).toBe(true);
    expect(matchesFilters(tanker, { ...DEFAULT_FILTERS, query: "211000999" })).toBe(true);
  });
});

it("counts data modes", () => {
  expect(dataModeCounts([feature("a"), feature("b", { data_mode: "synthetic" })])).toEqual({ real_time: 1, synthetic: 1 });
});

it("reconnect delay grows and caps", () => {
  expect([0, 1, 2, 10].map(reconnectDelay)).toEqual([1000, 2000, 4000, 30000]);
});

describe("regions", async () => {
  const { inBBox, regionById } = await import("@/lib/regions");
  it("looks up presets and falls back to world", () => {
    expect(regionById("ph").label).toBe("Philippines");
    expect(regionById("nope").id).toBe("world");
    expect(regionById(null).id).toBe("world");
  });
  it("tests membership, including antimeridian boxes", () => {
    expect(inBBox(120.97, 14.6, regionById("ph").bbox)).toBe(true); // Manila
    expect(inBBox(103.85, 1.28, regionById("ph").bbox)).toBe(false); // Singapore
    expect(inBBox(179, 0, [170, -10, -170, 10])).toBe(true);
    expect(inBBox(0, 0, [170, -10, -170, 10])).toBe(false);
  });
});
