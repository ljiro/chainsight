"use client";

import { ArrowRight, Search } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import { DelayBadge, ShipmentStatusBadge } from "@/components/shipments/badges";
import { ImportPanel } from "@/components/shipments/import-panel";
import { TrackForm } from "@/components/shipments/track-form";
import { EmptyState, ErrorState, LoadingRows } from "@/components/query-state";
import { StatusBadge } from "@/components/status-badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useCarriers, useShipments } from "@/lib/queries";
import type { ShipmentLocation } from "@/lib/types";
import { formatTime } from "@/lib/utils";

function Place({ loc }: { loc: ShipmentLocation }) {
  if (!loc.unlocode) return <span className="text-muted-foreground">—</span>;
  return (
    <span title={loc.mapped ? (loc.port_name ?? undefined) : "Not in the port registry with coordinates"}>
      <span className="font-mono">{loc.unlocode}</span>
      {!loc.mapped ? <span className="text-muted-foreground">*</span> : null}
    </span>
  );
}

export function ShipmentsView() {
  const carriers = useCarriers();
  const [q, setQ] = useState("");
  const [carrier, setCarrier] = useState("");
  const [delay, setDelay] = useState("");
  const [activeOnly, setActiveOnly] = useState(false);
  const params = useMemo(() => {
    const p = new URLSearchParams({ limit: "100" });
    if (q.trim().length >= 2) p.set("q", q.trim());
    if (carrier) p.set("carrier", carrier);
    if (delay) p.set("delay_status", delay);
    if (activeOnly) p.set("active_only", "true");
    return p;
  }, [q, carrier, delay, activeOnly]);
  const shipments = useShipments(params);
  const anyUnmapped = shipments.data?.items.some((s) => !s.origin.mapped || !s.destination.mapped);

  return (
    <div className="flex flex-col gap-6">
      <div className="grid gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Track a shipment</CardTitle>
            <CardDescription>
              Queries the carrier through its integration. Carriers without configured credentials say so instead of
              returning made-up data.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <TrackForm />
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Import shipments</CardTitle>
            <CardDescription>Bring your own milestone data as CSV.</CardDescription>
          </CardHeader>
          <CardContent>
            <ImportPanel />
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Tracked shipments</CardTitle>
          <CardDescription>
            Delay compares the latest estimate (or actual arrival) with the carrier&apos;s original planned arrival.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Shipment filters">
            <label className="relative">
              <span className="sr-only">Search shipments</span>
              <Search className="pointer-events-none absolute top-2 left-2.5 size-4 text-muted-foreground" aria-hidden />
              <input
                type="search"
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="Reference or container"
                className="h-8 w-56 rounded-md border bg-card pr-2 pl-8 text-sm"
              />
            </label>
            <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
              Carrier
              <select value={carrier} onChange={(e) => setCarrier(e.target.value)} className="h-8 w-44 rounded-md border bg-card px-2 text-sm text-foreground">
                <option value="">All</option>
                {(carriers.data?.items ?? []).map((c) => (
                  <option key={c.code} value={c.code}>
                    {c.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
              Delay
              <select value={delay} onChange={(e) => setDelay(e.target.value)} className="h-8 rounded-md border bg-card px-2 text-sm text-foreground">
                <option value="">Any</option>
                <option value="delayed">Delayed</option>
                <option value="on_time">On time</option>
                <option value="arrived_late">Arrived late</option>
                <option value="arrived_on_time">Arrived on time</option>
              </select>
            </label>
            <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <input type="checkbox" checked={activeOnly} onChange={(e) => setActiveOnly(e.target.checked)} />
              Active only
            </label>
          </div>

          {shipments.isPending ? (
            <LoadingRows rows={4} />
          ) : shipments.error ? (
            <ErrorState error={shipments.error} onRetry={() => void shipments.refetch()} />
          ) : shipments.data.items.length === 0 ? (
            <EmptyState title="No shipments tracked yet">
              Track a reference above, try a demo reference, or import a CSV.
            </EmptyState>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-[11px] tracking-wide text-muted-foreground uppercase">
                    <th className="py-2 pr-3 font-medium">Reference</th>
                    <th className="py-2 pr-3 font-medium">Carrier</th>
                    <th className="py-2 pr-3 font-medium">Route</th>
                    <th className="py-2 pr-3 font-medium">Status</th>
                    <th className="py-2 pr-3 font-medium">ETA (UTC)</th>
                    <th className="py-2 pr-3 font-medium">Delay</th>
                    <th className="py-2 font-medium">Data</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {shipments.data.items.map((s) => (
                    <tr key={s.id} data-testid={`shipment-${s.reference_value}`}>
                      <td className="py-2 pr-3">
                        <Link href={`/shipments/${s.id}`} className="font-medium hover:underline">
                          {s.reference_value}
                        </Link>
                        <p className="text-xs text-muted-foreground">
                          {s.containers.slice(0, 2).join(", ")}
                          {s.containers.length > 2 ? ` +${s.containers.length - 2}` : ""}
                        </p>
                      </td>
                      <td className="py-2 pr-3">{s.carrier.name}</td>
                      <td className="py-2 pr-3 whitespace-nowrap">
                        <Place loc={s.origin} /> <ArrowRight className="inline size-3 text-muted-foreground" aria-label="to" />{" "}
                        <Place loc={s.destination} />
                      </td>
                      <td className="py-2 pr-3">
                        <ShipmentStatusBadge status={s.status} />
                      </td>
                      <td className="py-2 pr-3 whitespace-nowrap">{formatTime(s.actual_arrival_at ?? s.current_eta_at)}</td>
                      <td className="py-2 pr-3">
                        <DelayBadge status={s.delay_status} hours={s.delay_hours} />
                      </td>
                      <td className="py-2">
                        <StatusBadge kind="mode" value={s.provenance.data_mode} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {anyUnmapped ? (
                <p className="mt-2 text-[11px] text-muted-foreground">
                  * Port code has no coordinates in the UN/LOCODE registry, so it cannot be shown on the map.
                </p>
              ) : null}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
