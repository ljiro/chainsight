"use client";

import { StatusBadge } from "@/components/status-badge";
import { EmptyState, ErrorState, LoadingRows } from "@/components/query-state";
import { useCarriers } from "@/lib/queries";
import { humanize } from "@/lib/utils";

export function CarrierStatusList({ compact = false }: { compact?: boolean }) {
  const { data, error, isPending, refetch } = useCarriers();

  if (isPending) return <LoadingRows rows={compact ? 4 : 8} />;
  if (error) return <ErrorState error={error} onRetry={() => void refetch()} />;
  if (data.items.length === 0) return <EmptyState title="No carriers registered" />;

  return (
    <ul className="divide-y">
      {data.items.map((c) => {
        const tracking = c.integrations.find((i) => i.capability === "shipment_tracking");
        return (
          <li key={c.code} className="flex items-center justify-between gap-3 py-2.5" data-testid={`carrier-${c.code}`}>
            <div className="min-w-0">
              <p className="truncate text-sm font-medium">{c.name}</p>
              <p className="text-xs text-muted-foreground">
                {c.scac ? <span className="font-mono">{c.scac}</span> : "No SCAC"} · {humanize(c.carrier_type)}
              </p>
              {!compact && tracking?.status_reason ? (
                <p className="mt-0.5 text-xs text-muted-foreground">{tracking.status_reason}</p>
              ) : null}
            </div>
            {tracking ? (
              <StatusBadge kind="integration" value={tracking.status} />
            ) : (
              <StatusBadge kind="integration" value="unavailable" />
            )}
          </li>
        );
      })}
    </ul>
  );
}
