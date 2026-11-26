"use client";

import { EmptyState, ErrorState, LoadingRows } from "@/components/query-state";
import { StatusBadge } from "@/components/status-badge";
import { Badge } from "@/components/ui/badge";
import { useCarrierPerformance } from "@/lib/queries";
import { formatAge } from "@/lib/utils";

export function CarrierComparison() {
  const { data, error, isPending, refetch } = useCarrierPerformance();
  if (isPending) return <LoadingRows rows={6} />;
  if (error) return <ErrorState error={error} onRetry={() => void refetch()} />;
  if (data.length === 0) return <EmptyState title="No carriers registered" />;

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm" aria-label="Carrier comparison">
        <thead>
          <tr className="text-left text-[11px] tracking-wide text-muted-foreground uppercase">
            <th className="py-2 pr-3 font-medium">Carrier</th>
            <th className="py-2 pr-3 font-medium">Tracking integration</th>
            <th className="py-2 pr-3 text-right font-medium" title="Vessels associated with this carrier in AIS data">
              Vessels (AIS)
            </th>
            <th className="py-2 pr-3 text-right font-medium">Shipments</th>
            <th className="py-2 pr-3 text-right font-medium">Delayed now</th>
            <th className="py-2 pr-3 text-right font-medium">On-time arrivals</th>
            <th className="py-2 text-right font-medium">Avg arrival delay</th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {data.map((c) => (
            <tr key={c.code} data-testid={`carrier-row-${c.code}`}>
              <td className="py-2.5 pr-3">
                <span className="font-medium">{c.name}</span>
                {c.code === "demo" ? (
                  <Badge tone="synthetic" className="ml-2">Synthetic</Badge>
                ) : null}
              </td>
              <td className="py-2.5 pr-3">
                {c.integration_status ? <StatusBadge kind="integration" value={c.integration_status} /> : "—"}
                {c.last_call_at ? (
                  <p className="mt-0.5 text-[11px] text-muted-foreground">
                    Last call {formatAge(c.last_call_at)}: {c.last_call_outcome}
                  </p>
                ) : null}
              </td>
              <td className="py-2.5 pr-3 text-right tabular-nums">{c.vessels_associated}</td>
              <td className="py-2.5 pr-3 text-right tabular-nums">
                {c.shipments_tracked}
                {c.shipments_active ? <span className="text-muted-foreground"> ({c.shipments_active} active)</span> : null}
              </td>
              <td className="py-2.5 pr-3 text-right tabular-nums">{c.shipments_delayed}</td>
              <td className="py-2.5 pr-3 text-right tabular-nums" title={c.arrivals ? `${c.arrivals} arrival(s)` : undefined}>
                {c.on_time_rate != null ? `${Math.round(c.on_time_rate * 100)}%` : "—"}
                {c.arrivals ? <span className="text-muted-foreground"> of {c.arrivals}</span> : null}
              </td>
              <td className="py-2.5 text-right tabular-nums">
                {c.avg_arrival_delay_hours != null ? `${c.avg_arrival_delay_hours} h` : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-2 text-[11px] text-muted-foreground">
        Reliability uses only shipments tracked in this workspace; small samples are not representative of a carrier.
        Vessel counts come from name-based AIS associations (inferred, 60% confidence).
      </p>
    </div>
  );
}
