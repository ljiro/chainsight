/** Map region presets. Bounding boxes are [west, south, east, north] in degrees. */
export type BBox = [number, number, number, number];

export interface Region {
  id: string;
  label: string;
  bbox: BBox;
}

export const REGIONS: Region[] = [
  { id: "world", label: "World", bbox: [-180, -70, 180, 75] },
  // Philippine archipelago plus approaches (Luzon Strait to the Celebes Sea).
  { id: "ph", label: "Philippines", bbox: [114, 4, 128, 22] },
  { id: "sea", label: "Southeast Asia", bbox: [92, -11, 135, 24] },
  { id: "north-sea", label: "North Sea & Channel", bbox: [-6, 48, 13, 61] },
];

export const WORLD = REGIONS[0]!;

export function regionById(id: string | null | undefined): Region {
  return REGIONS.find((r) => r.id === id) ?? WORLD;
}

export function inBBox(lon: number, lat: number, [w, s, e, n]: BBox): boolean {
  if (lat < s || lat > n) return false;
  return w <= e ? lon >= w && lon <= e : lon >= w || lon <= e; // supports antimeridian boxes
}

/** Below this many reporting vessels, the UI warns that AIS coverage is thin. */
export const LOW_COVERAGE_THRESHOLD = 20;
