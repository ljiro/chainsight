"use client";

import { AlertOctagon, AlertTriangle, Info, Package, Ship } from "lucide-react";
import Link from "next/link";
import { KpiCard } from "@/components/dashboard/kpi-card";
import { EmptyState, ErrorState, LoadingRows } from "@/components/query-state";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useAlerts, useApproaching, useFleetSummary, useShipmentsSummary } from "@/lib/queries";
import type { Alert } from "@/lib/types";
import { formatAge, humanize } from "@/lib/utils";

export function MonitoredVesselsKpi() {
  const { data, error, isPending } = useFleetSummary();
  const synthetic = (data?.by_data_mode.synthetic ?? 0) > 0;
  return (
    <KpiCard
      label="Monitored vessels"
      value={data && data.vessels_total > 0 ? data.vessels_recent : null}
      icon={Ship}
      unavailableReason={
        isPending ? "Loading…" : error ? "Fleet data unavailable" : "No AIS data yet: start the AIS consumer"
      }
      tag={synthetic ? <Badge tone="synthetic">Synthetic</Badge> : null}
    />
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="mt-0.5 text-xl font-semibold tabular-nums">{value.toLocaleString()}</p>
    </div>
  );
}

export function FleetActivityCard() {
  const { data, error, isPending, refetch } = useFleetSummary();
  const classes = Object.entries(data?.by_class ?? {}).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...classes.map(([, n]) => n));
  const modes = Object.entries(data?.by_data_mode ?? {});

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-2">
          <CardTitle>Fleet activity</CardTitle>
          <Link href="/fleet" className="text-xs font-medium text-primary hover:underline">Open map</Link>
        </div>
        <CardDescription>
          Vessels reporting in the last hour
          {modes.length ? ` · data: ${modes.map(([m, n]) => `${n} ${humanize(m).toLowerCase()}`).join(", ")}` : ""}
        </CardDescription>
      </CardHeader>
      <CardContent>
        {isPending ? (
          <LoadingRows rows={3} />
        ) : error ? (
          <ErrorState error={error} onRetry={() => void refetch()} />
        ) : data.vessels_total === 0 ? (
          <EmptyState title="No vessels tracked yet">Start AIS ingestion to populate fleet activity.</EmptyState>
        ) : (
          <div className="grid gap-5 sm:grid-cols-2">
            <div className="grid grid-cols-2 gap-4">
              <Stat label="Underway" value={data.underway} />
              <Stat label="At anchor / moored" value={data.at_anchor_or_moored} />
              <Stat label="In port (geofence)" value={data.in_port} />
              <Stat label="Port calls, 24 h" value={data.port_calls_last_24h} />
            </div>
            <figure aria-label="Vessels by class, last hour">
              <figcaption className="mb-2 text-xs text-muted-foreground">By class (from AIS ship type)</figcaption>
              <ul className="flex flex-col gap-1.5">
                {classes.map(([cls, n]) => (
                  <li key={cls} className="grid grid-cols-[6.5rem_1fr_2.5rem] items-center gap-2 text-xs" title={`${humanize(cls)}: ${n}`}>
                    <span className="truncate">{humanize(cls)}</span>
                    <span className="h-2 rounded-r-[4px] bg-[#2a78d6] dark:bg-[#3987e5]" style={{ width: `${(n / max) * 100}%` }} />
                    <span className="text-right tabular-nums">{n}</span>
                  </li>
                ))}
              </ul>
            </figure>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export function ApproachingCard() {
  const { data, error, isPending } = useApproaching();
  return (
    <Card>
      <CardHeader>
        <CardTitle>Approaching ports</CardTitle>
        <CardDescription>Underway vessels whose course points at a port within 30 nm.</CardDescription>
      </CardHeader>
      <CardContent>
        {isPending ? (
          <LoadingRows rows={3} />
        ) : error ? (
          <ErrorState error={error} />
        ) : data.length === 0 ? (
          <EmptyState title="No vessels approaching ports right now" />
        ) : (
          <ul className="divide-y">
            {data.map((a) => (
              <li key={a.vessel.id} className="flex items-center justify-between gap-3 py-2 text-sm">
                <Link href={`/vessels/${a.vessel.id}`} className="min-w-0 truncate font-medium hover:underline">
                  {a.vessel.name ?? `MMSI ${a.vessel.mmsi}`}
                </Link>
                <span className="shrink-0 text-xs text-muted-foreground">
                  {a.port.name} · {a.distance_nm} nm
                </span>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

const SEVERITY: Record<Alert["severity"], { icon: typeof Info; tone: "info" | "warning" | "danger"; label: string }> = {
  info: { icon: Info, tone: "info", label: "Info" },
  warning: { icon: AlertTriangle, tone: "warning", label: "Warning" },
  critical: { icon: AlertOctagon, tone: "danger", label: "Critical" },
};

export function AlertsCard() {
  const { data, error, isPending } = useAlerts();
  return (
    <Card>
      <CardHeader>
        <CardTitle>Open alerts</CardTitle>
        <CardDescription>Data freshness and ingestion alerts raised by the platform.</CardDescription>
      </CardHeader>
      <CardContent>
        {isPending ? (
          <LoadingRows rows={2} />
        ) : error ? (
          <ErrorState error={error} />
        ) : data.items.length === 0 ? (
          <EmptyState title="No open alerts" />
        ) : (
          <ul className="divide-y">
            {data.items.map((a) => {
              const s = SEVERITY[a.severity];
              return (
                <li key={a.id} className="flex items-start gap-3 py-2.5">
                  <Badge tone={s.tone}>
                    <s.icon className="size-3" aria-hidden />
                    {s.label}
                  </Badge>
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium">{a.title}</p>
                    {a.message ? <p className="text-xs text-muted-foreground">{a.message}</p> : null}
                  </div>
                  <span className="shrink-0 text-xs text-muted-foreground">{formatAge(a.raised_at)}</span>
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

export function ShipmentKpis() {
  const { data, error, isPending } = useShipmentsSummary();
  const synthetic = (data?.by_data_mode.synthetic ?? 0) > 0;
  const tag = synthetic ? <Badge tone="synthetic">incl. synthetic</Badge> : null;
  const reason = isPending ? "Loading…" : error ? "Shipment data unavailable" : "Track or import a shipment";
  const has = !!data && data.total > 0;
  return (
    <>
      <KpiCard label="Active shipments" value={has ? data.active : null} icon={Package} unavailableReason={reason} tag={tag} />
      <KpiCard
        label="Delayed shipments"
        value={has ? data.delayed : null}
        icon={AlertTriangle}
        unavailableReason={reason}
        tag={tag}
      />
    </>
  );
}
