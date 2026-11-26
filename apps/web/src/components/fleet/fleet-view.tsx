"use client";

import { useTheme } from "next-themes";
import { useCallback, useEffect, useMemo, useState } from "react";
import { FleetFilters } from "@/components/fleet/fleet-filters";
import { FleetMap, type Viewport } from "@/components/fleet/fleet-map";
import { ProvenanceBanner } from "@/components/fleet/provenance-banner";
import { VesselPanel } from "@/components/fleet/vessel-panel";
import { EmptyState, ErrorState } from "@/components/query-state";
import { StatusBadge } from "@/components/status-badge";
import {
  CLASS_GROUPS,
  DEFAULT_FILTERS,
  GROUP_COLORS,
  applyLiveUpdate,
  bboxParam,
  classGroup,
  dataModeCounts,
  matchesFilters,
  mergeSnapshot,
  refreshAges,
  type FleetFilterState,
  type VesselIndex,
} from "@/lib/fleet";
import { useLivePositions } from "@/lib/live";
import { useCarriers, useFleetPositions, usePorts, useVessel, useVesselTrack } from "@/lib/queries";
import type { LivePositionMessage } from "@/lib/types";

const PORT_ZOOM = 4;

export function FleetView() {
  const { resolvedTheme } = useTheme();
  const mode = resolvedTheme === "dark" ? "dark" : "light";
  const [filters, setFilters] = useState<FleetFilterState>(DEFAULT_FILTERS);
  const [index, setIndex] = useState<VesselIndex>(new Map());
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [focus, setFocus] = useState<{ lon: number; lat: number; key: string } | null>(null);
  const [viewport, setViewport] = useState<Viewport | null>(null);
  const [trackHours, setTrackHours] = useState(24);

  const onLive = useCallback((msg: LivePositionMessage) => setIndex((prev) => applyLiveUpdate(prev, msg)), []);
  const live = useLivePositions(onLive);
  const positions = useFleetPositions(live === "live" ? 60_000 : 15_000);
  const carriers = useCarriers();
  const vessel = useVessel(selectedId);
  const track = useVesselTrack(selectedId, trackHours);
  const ports = usePorts(viewport && viewport.zoom >= PORT_ZOOM ? bboxParam(viewport.bbox) : null);

  useEffect(() => {
    if (positions.data) setIndex((prev) => mergeSnapshot(prev, positions.data));
  }, [positions.data]);

  // Positions age while on screen; recompute freshness so nothing stays "live" forever.
  useEffect(() => {
    const t = setInterval(() => setIndex((prev) => refreshAges(prev)), 15_000);
    return () => clearInterval(t);
  }, []);

  const all = useMemo(() => [...index.values()], [index]);
  const visible = useMemo(() => all.filter((f) => matchesFilters(f, filters)), [all, filters]);
  const inView = useMemo(() => {
    if (!viewport) return visible;
    const [w, s, e, n] = viewport.bbox;
    return visible.filter(({ geometry: { coordinates: [lon, lat] } }) =>
      lat >= s && lat <= n && (w <= e ? lon >= w && lon <= e : lon >= w || lon <= e),
    );
  }, [visible, viewport]);

  const select = (id: string | null) => {
    setSelectedId(id);
    const f = id ? index.get(id) : null;
    if (f) setFocus({ lon: f.geometry.coordinates[0], lat: f.geometry.coordinates[1], key: `${id}-${Date.now()}` });
  };

  const carrierOptions = (carriers.data?.items ?? []).map((c) => ({ code: c.code, name: c.name }));
  const counts = dataModeCounts(visible);

  return (
    <div className="flex flex-col gap-3">
      <ProvenanceBanner counts={counts} live={live} updatedAt={positions.data?.meta.generated_at ?? null} />
      <FleetFilters value={filters} onChange={setFilters} carriers={carrierOptions} mode={mode} />

      <div className="grid gap-3 lg:grid-cols-[1fr_20rem]">
        <div className="relative min-h-[28rem] overflow-hidden rounded-lg border bg-muted lg:h-[calc(100dvh-16rem)]">
          <FleetMap
            className="absolute inset-0"
            vessels={visible}
            ports={ports.data ?? []}
            track={selectedId ? track.data : null}
            selectedId={selectedId}
            focus={focus}
            onSelect={select}
            onViewportChange={setViewport}
          />
          <div className="pointer-events-none absolute bottom-2 left-2 flex flex-col gap-1 rounded-md bg-card/90 px-2.5 py-2 text-[11px] shadow-sm">
            <span className="font-medium">Vessel class</span>
            {CLASS_GROUPS.map((g) => (
              <span key={g.key} className="flex items-center gap-1.5">
                <span aria-hidden className="size-2.5 rounded-full" style={{ background: GROUP_COLORS[mode][g.key] }} />
                {g.label}
              </span>
            ))}
            <span className="mt-1 text-muted-foreground">Faded = not seen in the last hour</span>
            {viewport && viewport.zoom < PORT_ZOOM ? (
              <span className="text-muted-foreground">Zoom in to show ports</span>
            ) : null}
          </div>
          {positions.isPending && index.size === 0 ? (
            <div role="status" className="absolute inset-0 grid place-items-center text-sm text-muted-foreground">
              Loading vessel positions…
            </div>
          ) : null}
        </div>

        <div className="flex min-w-0 flex-col gap-3">
          {selectedId ? (
            <VesselPanel
              vessel={vessel.data}
              isPending={vessel.isPending}
              error={vessel.error}
              onClose={() => setSelectedId(null)}
              trackHours={trackHours}
              onTrackHours={setTrackHours}
              trackInfo={track.data ? { points: track.data.total_points, gaps: track.data.gaps.length } : null}
            />
          ) : null}
          <section aria-label="Vessels in view" className="flex min-h-0 flex-col rounded-lg border bg-card">
            <header className="flex items-baseline justify-between border-b px-4 py-2.5">
              <h2 className="text-sm font-semibold">Vessels in view</h2>
              <span className="text-xs text-muted-foreground tabular-nums">
                {inView.length.toLocaleString()} of {all.length.toLocaleString()}
              </span>
            </header>
            {positions.error && index.size === 0 ? (
              <div className="p-3">
                <ErrorState error={positions.error} onRetry={() => void positions.refetch()} />
              </div>
            ) : all.length === 0 && !positions.isPending ? (
              <div className="p-3">
                <EmptyState title="No vessel positions in the last 24 hours">
                  Start AIS ingestion: <code className="font-mono">make ais</code> with{" "}
                  <code className="font-mono">AIS_PROVIDER=simulator</code> (synthetic) or{" "}
                  <code className="font-mono">aisstream</code> (live, needs an API key).
                </EmptyState>
              </div>
            ) : (
              <ul className="max-h-[24rem] divide-y overflow-y-auto lg:max-h-[calc(100dvh-24rem)]">
                {inView
                  .slice()
                  .sort((a, b) => (a.properties.name ?? "").localeCompare(b.properties.name ?? ""))
                  .slice(0, 200)
                  .map((f) => (
                    <li key={f.id}>
                      <button
                        type="button"
                        onClick={() => select(f.id)}
                        aria-current={f.id === selectedId ? "true" : undefined}
                        className="flex w-full items-center gap-2 px-4 py-2 text-left text-sm hover:bg-accent/60 aria-[current]:bg-accent"
                      >
                        <span
                          aria-hidden
                          className="size-2.5 shrink-0 rounded-full"
                          style={{ background: GROUP_COLORS[mode][classGroup(f.properties.vessel_class)] }}
                        />
                        <span className="min-w-0 flex-1 truncate">{f.properties.name ?? `MMSI ${f.properties.mmsi}`}</span>
                        <span className="shrink-0 text-xs text-muted-foreground tabular-nums">
                          {f.properties.sog_knots ?? "—"} kn
                        </span>
                        <StatusBadge kind="position" value={f.properties.freshness} />
                      </button>
                    </li>
                  ))}
              </ul>
            )}
          </section>
        </div>
      </div>
      <p className="text-[11px] text-muted-foreground">
        Basemap © OpenFreeMap / OpenStreetMap contributors. Port registry: UN/LOCODE (UNECE). Vessel positions:
        source shown per vessel.
      </p>
    </div>
  );
}
