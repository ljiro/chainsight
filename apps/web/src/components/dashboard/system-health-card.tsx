"use client";

import { StatusBadge } from "@/components/status-badge";
import { ErrorState, LoadingRows } from "@/components/query-state";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useReadiness } from "@/lib/queries";
import { humanize } from "@/lib/utils";

function detailLine(detail: Record<string, unknown>): string | null {
  const parts: string[] = [];
  if (typeof detail.postgis === "string") parts.push(`PostGIS ${detail.postgis}`);
  if ("timescaledb" in detail)
    parts.push(detail.timescaledb ? `TimescaleDB ${String(detail.timescaledb)}` : "TimescaleDB unavailable");
  if (typeof detail.migration_revision === "string") parts.push(`schema rev ${detail.migration_revision}`);
  const issues = Array.isArray(detail.issues) ? (detail.issues as string[]) : [];
  if (issues.length) parts.push(issues.join(", "));
  if (typeof detail.error === "string") parts.push(detail.error);
  return parts.length ? parts.join(" · ") : null;
}

export function SystemHealthCard() {
  const { data, error, isPending, refetch } = useReadiness();

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle>Platform health</CardTitle>
          {data ? <StatusBadge kind="health" value={data.status} /> : null}
        </div>
        <CardDescription>Live readiness of ChainSight&apos;s own infrastructure.</CardDescription>
      </CardHeader>
      <CardContent>
        {isPending ? (
          <LoadingRows rows={2} />
        ) : error ? (
          <ErrorState error={error} onRetry={() => void refetch()} />
        ) : (
          <ul className="divide-y">
            {data.components.map((c) => (
              <li key={c.name} className="flex items-center justify-between gap-3 py-2.5">
                <div className="min-w-0">
                  <p className="text-sm font-medium">{humanize(c.name)}</p>
                  {detailLine(c.detail) ? (
                    <p className="truncate text-xs text-muted-foreground">{detailLine(c.detail)}</p>
                  ) : null}
                </div>
                <div className="flex shrink-0 items-center gap-3">
                  {c.latency_ms !== null ? (
                    <span className="font-mono text-[11px] text-muted-foreground">{c.latency_ms.toFixed(0)} ms</span>
                  ) : null}
                  <StatusBadge kind="health" value={c.status} />
                </div>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
