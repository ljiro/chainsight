"use client";

import { AlertTriangle, ShieldAlert } from "lucide-react";
import Link from "next/link";
import { KpiCard } from "@/components/dashboard/kpi-card";
import { ModelTag, SyntheticModelBadge } from "@/components/predictions/model-tag";
import { EmptyState, ErrorState, LoadingRows } from "@/components/query-state";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { fallbackLabel, formatProbability, formatRelativeHours, intervalLabel, riskLevel } from "@/lib/predictions";
import { useAnomalies, usePredictionsSummary, useShipmentDelayRisk, useVesselEta } from "@/lib/queries";
import type { DelayPrediction, EtaPrediction, VesselAnomaly } from "@/lib/types";
import { formatAge, formatTime, humanize } from "@/lib/utils";

export function VesselEtaCard({ vesselId }: { vesselId: string }) {
  const { data, error, isPending, refetch } = useVesselEta(vesselId);
  return (
    <Card>
      <CardHeader>
        <CardTitle>Predicted arrival</CardTitle>
        <CardDescription>
          Toward the AIS-reported destination, when it resolves to a known port. Every prediction is kept.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {isPending ? (
          <LoadingRows rows={2} />
        ) : error ? (
          <ErrorState error={error} onRetry={() => void refetch()} />
        ) : data.length === 0 ? (
          <EmptyState title="No ETA prediction">
            Predictions need a destination that resolves to a known port and a recent position.
          </EmptyState>
        ) : (
          <EtaDetail rows={data} />
        )}
      </CardContent>
    </Card>
  );
}

function EtaDetail({ rows }: { rows: EtaPrediction[] }) {
  const [p] = rows;
  if (!p) return null;
  const interval = intervalLabel(p);
  const fallback = fallbackLabel(p.fallback_reason);
  return (
    <div className="flex flex-col gap-3 text-sm" data-testid="vessel-eta">
      <div>
        <p className="text-xs text-muted-foreground">
          {p.target_port_name ?? p.target_unlocode} <span className="font-mono">{p.target_unlocode}</span>
        </p>
        <p className="text-lg font-semibold tabular-nums">{formatTime(p.predicted_arrival_at)}</p>
        <p className="text-xs text-muted-foreground">{formatRelativeHours(p.predicted_arrival_at)}</p>
      </div>
      {p.lower_bound_at && p.upper_bound_at ? (
        <div>
          <p className="text-xs tabular-nums">
            {formatTime(p.lower_bound_at)} – {formatTime(p.upper_bound_at)}
          </p>
          {interval ? <p className="text-[11px] text-muted-foreground">{interval}</p> : null}
        </div>
      ) : null}
      {fallback ? <p className="rounded-md border border-warning/30 bg-warning/5 p-2 text-[11px]">{fallback}</p> : null}
      {p.actual_arrival_at ? (
        <p className="text-xs">
          Arrived {formatTime(p.actual_arrival_at)}
          {p.error_hours != null ? ` · error ${p.error_hours > 0 ? "+" : ""}${p.error_hours.toFixed(1)} h` : ""}
        </p>
      ) : null}
      <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
        <dt>Predicted</dt>
        <dd>{formatAge(p.predicted_at)}</dd>
        <dt>Input data as of</dt>
        <dd>{formatTime(p.input_data_as_of)}</dd>
        {p.remaining_distance_nm != null ? (
          <>
            <dt>Remaining distance</dt>
            <dd className="tabular-nums">{Math.round(p.remaining_distance_nm)} nm (great-circle)</dd>
          </>
        ) : null}
      </dl>
      <ModelTag version={p.model_version} synthetic={p.is_synthetic_trained} />
      {rows.length > 1 ? (
        <details className="text-xs">
          <summary className="cursor-pointer text-muted-foreground">Prediction history ({rows.length})</summary>
          <ol className="mt-2 flex flex-col gap-1">
            {rows.map((r) => (
              <li key={r.id} className="flex justify-between gap-2 tabular-nums">
                <span>{formatTime(r.predicted_arrival_at)}</span>
                <span className="text-muted-foreground">made {formatTime(r.predicted_at)}</span>
              </li>
            ))}
          </ol>
        </details>
      ) : null}
    </div>
  );
}

const ANOMALY_LABELS: Record<string, string> = {
  unexpected_stop: "Unexpected stop",
  speed_drop: "Speed drop",
  ais_silence: "AIS silence",
  long_port_wait: "Long port wait",
  route_deviation: "Route deviation",
};

export function anomalyLabel(type: string): string {
  return ANOMALY_LABELS[type] ?? humanize(type);
}

export function AnomalyItem({ a, showVessel = false }: { a: VesselAnomaly; showVessel?: boolean }) {
  return (
    <li className="rounded-md border p-3 text-sm" data-testid="anomaly">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={a.resolved_at ? "neutral" : a.severity === "critical" ? "danger" : "warning"}>
          <AlertTriangle className="size-3" aria-hidden />
          {anomalyLabel(a.type)}
        </Badge>
        {showVessel ? (
          <Link href={`/vessels/${a.vessel_id}`} className="font-medium hover:underline">
            {a.vessel_name ?? "Unnamed vessel"}
          </Link>
        ) : null}
        <span className="ml-auto text-[11px] text-muted-foreground">
          {a.resolved_at ? `resolved ${formatAge(a.resolved_at)}` : `raised ${formatAge(a.raised_at)}`}
        </span>
      </div>
      <p className="mt-1 text-xs text-muted-foreground">{a.message}</p>
    </li>
  );
}

export function VesselAnomaliesCard({ vesselId }: { vesselId: string }) {
  const { data, error, isPending, refetch } = useAnomalies(vesselId, "all");
  return (
    <Card>
      <CardHeader>
        <CardTitle>Anomaly alerts</CardTitle>
        <CardDescription>
          Rule-based checks (unexpected stops, speed drops, AIS silence, long port waits). Only vessels linked to a
          carrier or shipment are monitored.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {isPending ? (
          <LoadingRows rows={2} />
        ) : error ? (
          <ErrorState error={error} onRetry={() => void refetch()} />
        ) : data.length === 0 ? (
          <p className="text-sm text-muted-foreground">No anomalies raised for this vessel.</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {data.slice(0, 8).map((a) => (
              <AnomalyItem key={a.id} a={a} />
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

export function ShipmentDelayCard({ shipmentId }: { shipmentId: string }) {
  const { data, error, isPending, refetch } = useShipmentDelayRisk(shipmentId);
  return (
    <Card>
      <CardHeader>
        <CardTitle>Delay risk</CardTitle>
        <CardDescription>
          Probability of arriving more than the threshold later than the original plan, using only events known at
          prediction time.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {isPending ? (
          <LoadingRows rows={2} />
        ) : error ? (
          <ErrorState error={error} onRetry={() => void refetch()} />
        ) : data.length === 0 ? (
          <EmptyState title="No delay prediction">
            No delay model is in production yet, or this shipment&apos;s data source is not one the model is allowed to
            score. A model trained on synthetic journeys only scores synthetic shipments.
          </EmptyState>
        ) : (
          <DelayDetail rows={data} />
        )}
      </CardContent>
    </Card>
  );
}

function DelayDetail({ rows }: { rows: DelayPrediction[] }) {
  const [p] = rows;
  if (!p) return null;
  const r = riskLevel(p.delay_probability);
  return (
    <div className="flex flex-col gap-3 text-sm" data-testid="shipment-delay">
      <div className="flex items-center gap-2">
        <p className="text-2xl font-semibold tabular-nums">{formatProbability(p.delay_probability)}</p>
        <Badge tone={r.tone}>
          <ShieldAlert className="size-3" aria-hidden />
          {humanize(r.level)} risk
        </Badge>
      </div>
      <p className="text-xs text-muted-foreground">
        of a delay over {p.threshold_hours} h{p.calibration_method ? ` · calibrated (${p.calibration_method})` : ""}
      </p>
      {p.factors.length ? (
        <div>
          <p className="text-xs font-medium">Contributing signals</p>
          <ul className="mt-1 list-disc pl-4 text-xs text-muted-foreground">
            {p.factors.map((f) => (
              <li key={f.factor}>{f.text}</li>
            ))}
          </ul>
        </div>
      ) : (
        <p className="text-xs text-muted-foreground">No carrier signal points to a delay.</p>
      )}
      {p.actual_delayed != null ? (
        <p className="text-xs">
          Outcome: {p.actual_delayed ? "delayed" : "not delayed"}
          {p.actual_delay_hours != null ? ` (${p.actual_delay_hours.toFixed(1)} h)` : ""}
        </p>
      ) : null}
      <p className="text-[11px] text-muted-foreground">
        Predicted {formatAge(p.predicted_at)} · events as of {formatTime(p.input_data_as_of)}
      </p>
      <ModelTag version={p.model_version} synthetic={p.is_synthetic_trained} />
    </div>
  );
}

export function HighRiskKpi() {
  const { data, isPending } = usePredictionsSummary();
  const noModel = data != null && data.delay_model_synthetic == null;
  return (
    <KpiCard
      label="High-risk shipments"
      value={isPending || !data || noModel ? null : data.high_risk}
      icon={ShieldAlert}
      unavailableReason={isPending ? "Loading…" : "No delay model in production"}
      tag={data?.delay_model_synthetic ? <SyntheticModelBadge /> : undefined}
    />
  );
}
