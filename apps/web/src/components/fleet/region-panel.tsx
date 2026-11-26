"use client";

import { AlertTriangle, Anchor } from "lucide-react";
import { LoadingRows } from "@/components/query-state";
import { LOW_COVERAGE_THRESHOLD, type Region } from "@/lib/regions";
import type { Port, VesselFeature } from "@/lib/types";

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <p className="text-[11px] text-muted-foreground">{label}</p>
      <p className="text-lg font-semibold tabular-nums">{value.toLocaleString()}</p>
    </div>
  );
}

/** Summary for a selected region: what is reporting, where vessels are in port, and
 *  an explicit warning when AIS coverage is too thin to be representative. */
export function RegionPanel({
  region,
  vessels,
  ports,
  portsPending,
  onFocusPort,
}: {
  region: Region;
  vessels: VesselFeature[];
  ports: Port[] | undefined;
  portsPending: boolean;
  onFocusPort: (p: Port) => void;
}) {
  const underway = vessels.filter((v) => (v.properties.sog_knots ?? 0) > 1 && ![1, 5].includes(v.properties.nav_status ?? -1)).length;
  const live = vessels.filter((v) => v.properties.freshness === "live").length;
  const busiest = (ports ?? []).filter((p) => p.vessels_in_port > 0).slice(0, 8);

  return (
    <section aria-label={`${region.label} summary`} className="flex flex-col gap-3 rounded-lg border bg-card p-4">
      <h2 className="text-sm font-semibold">{region.label}</h2>
      <div className="grid grid-cols-3 gap-2">
        <Stat label="Vessels (24 h)" value={vessels.length} />
        <Stat label="Live (10 min)" value={live} />
        <Stat label="Underway" value={underway} />
      </div>

      {vessels.length < LOW_COVERAGE_THRESHOLD ? (
        <p role="note" data-testid="low-coverage" className="flex gap-2 rounded-md border border-warning/30 bg-warning/10 p-2 text-xs">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-warning" aria-hidden />
          <span>
            <span className="font-medium">Low AIS coverage.</span> Only {vessels.length} vessel
            {vessels.length === 1 ? "" : "s"} reported here in the last 24 h. The current feed relies on shore-based
            receivers, so this is not the real traffic level. Satellite AIS or a local receiver feed would be needed
            for representative coverage.
          </span>
        </p>
      ) : null}

      <div>
        <h3 className="mb-1.5 text-xs font-medium text-muted-foreground">Ports with vessels in port (AIS geofence)</h3>
        {portsPending ? (
          <LoadingRows rows={2} />
        ) : busiest.length === 0 ? (
          <p className="text-xs text-muted-foreground">No vessels currently detected in port.</p>
        ) : (
          <ul className="divide-y text-sm">
            {busiest.map((p) => (
              <li key={p.id}>
                <button type="button" onClick={() => onFocusPort(p)} className="flex w-full items-center gap-2 py-1.5 text-left hover:text-primary">
                  <Anchor className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
                  <span className="min-w-0 flex-1 truncate">{p.name}</span>
                  <span className="font-mono text-[11px] text-muted-foreground">{p.unlocode}</span>
                  <span className="w-6 text-right tabular-nums">{p.vessels_in_port}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
