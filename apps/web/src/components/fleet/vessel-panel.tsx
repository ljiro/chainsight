"use client";

import { ArrowUpRight, X } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";
import { ErrorState, LoadingRows } from "@/components/query-state";
import { StatusBadge } from "@/components/status-badge";
import { Badge } from "@/components/ui/badge";
import type { CarrierAssociation, VesselDetail } from "@/lib/types";
import { formatAge, formatCoord, humanize } from "@/lib/utils";

export function CarrierAssociationText({ carrier }: { carrier: CarrierAssociation | null }) {
  if (!carrier) return <span className="text-muted-foreground">No known operator</span>;
  const how =
    carrier.method === "name_heuristic"
      ? "Inferred from vessel name"
      : carrier.method === "carrier_api"
        ? "Reported by carrier API"
        : "Set manually";
  return (
    <span>
      <span className="font-medium">{carrier.carrier_name}</span>{" "}
      <span className="text-muted-foreground">
        ({humanize(carrier.relation)} · {how} · {Math.round(carrier.confidence * 100)}% confidence)
      </span>
    </span>
  );
}

export function VesselClassText({ vessel }: { vessel: Pick<VesselDetail, "vessel_class" | "vessel_class_source"> }) {
  const src =
    vessel.vessel_class_source === "ais_ship_type"
      ? "from AIS ship type"
      : vessel.vessel_class_source
        ? `from ${humanize(vessel.vessel_class_source)}`
        : "not reported";
  return (
    <span>
      {humanize(vessel.vessel_class)} <span className="text-muted-foreground">({src})</span>
    </span>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[7.5rem_1fr] gap-2 py-1.5 text-sm">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </div>
  );
}

export function VesselPanel({
  vessel,
  isPending,
  error,
  onClose,
  trackHours,
  onTrackHours,
  trackInfo,
}: {
  vessel: VesselDetail | undefined;
  isPending: boolean;
  error: unknown;
  onClose: () => void;
  trackHours: number;
  onTrackHours: (h: number) => void;
  trackInfo: { points: number; gaps: number } | null;
}) {
  return (
    <aside aria-label="Vessel details" className="flex flex-col gap-3 rounded-lg border bg-card p-4">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h2 className="truncate text-base font-semibold">{vessel?.name ?? (isPending ? "Loading…" : "Unknown vessel")}</h2>
          {vessel ? (
            <p className="font-mono text-xs text-muted-foreground">
              MMSI {vessel.mmsi ?? "—"} · IMO {vessel.imo ?? "—"}
            </p>
          ) : null}
        </div>
        <button type="button" onClick={onClose} aria-label="Close vessel details" className="rounded p-1 hover:bg-accent">
          <X className="size-4" />
        </button>
      </div>

      {isPending ? <LoadingRows rows={4} /> : null}
      {error ? <ErrorState error={error} /> : null}

      {vessel ? (
        <>
          <div className="flex flex-wrap gap-1.5">
            {vessel.position ? <StatusBadge kind="position" value={vessel.position.freshness} /> : null}
            {vessel.position?.provenance.data_mode ? (
              <StatusBadge kind="mode" value={vessel.position.provenance.data_mode} />
            ) : null}
            {vessel.open_port_call ? <Badge tone="info">In port · {vessel.open_port_call.port.name}</Badge> : null}
          </div>
          <dl className="divide-y">
            <Row label="Class">
              <VesselClassText vessel={vessel} />
            </Row>
            <Row label="Operator">
              <CarrierAssociationText carrier={vessel.carrier} />
            </Row>
            {vessel.position ? (
              <>
                <Row label="Position">
                  <span className="font-mono text-xs">{formatCoord(vessel.position.lat, vessel.position.lon)}</span>
                </Row>
                <Row label="Speed / course">
                  {vessel.position.sog_knots ?? "—"} kn · {vessel.position.cog_deg ?? "—"}°
                </Row>
                <Row label="Status">{vessel.position.nav_status_label ?? "—"}</Row>
                <Row label="Last report">
                  {formatAge(vessel.position.observed_at)}{" "}
                  <span className="text-muted-foreground">via {vessel.position.provenance.source}</span>
                </Row>
              </>
            ) : (
              <Row label="Position">No position reported</Row>
            )}
            <Row label="Destination">
              {vessel.destination ?? "—"} <span className="text-muted-foreground">(as reported in AIS)</span>
            </Row>
          </dl>

          <div className="flex items-center justify-between gap-2 text-xs">
            <label className="flex items-center gap-1.5 text-muted-foreground">
              Track
              <select
                value={trackHours}
                onChange={(e) => onTrackHours(Number(e.target.value))}
                className="h-7 rounded border bg-card px-1.5 text-foreground"
              >
                <option value={6}>6 h</option>
                <option value={24}>24 h</option>
                <option value={72}>3 d</option>
                <option value={168}>7 d</option>
              </select>
            </label>
            {trackInfo ? (
              <span className="text-muted-foreground">
                {trackInfo.points} points{trackInfo.gaps ? ` · ${trackInfo.gaps} AIS gap(s)` : ""}
              </span>
            ) : null}
          </div>

          <p className="text-[11px] text-muted-foreground">
            AIS shows where this vessel is. It does not show where any individual container is.
          </p>
          <Link
            href={`/vessels/${vessel.id}`}
            className="inline-flex items-center gap-1 text-sm font-medium text-primary hover:underline"
          >
            Open vessel page <ArrowUpRight className="size-3.5" aria-hidden />
          </Link>
        </>
      ) : null}
    </aside>
  );
}
