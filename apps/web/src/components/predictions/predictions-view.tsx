"use client";

import { Activity, Anchor, Clock, ShieldAlert } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { KpiCard } from "@/components/dashboard/kpi-card";
import { CongestionChart } from "@/components/predictions/congestion-chart";
import { ModelTag } from "@/components/predictions/model-tag";
import { AnomalyItem, HighRiskKpi } from "@/components/predictions/prediction-cards";
import { EmptyState, ErrorState, LoadingRows } from "@/components/query-state";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  MODEL_LABELS,
  fallbackLabel,
  formatProbability,
  formatRelativeHours,
  headlineMetrics,
  intervalLabel,
  isRuleModel,
  formatRange,
  riskLevel,
  shortIntervalLabel,
} from "@/lib/predictions";
import {
  useAnomalies,
  useCongestion,
  useDelayPredictions,
  useEtaPredictions,
  useModels,
  usePortCongestion,
  usePredictionsSummary,
} from "@/lib/queries";
import type { ModelVersion } from "@/lib/types";
import { formatAge, formatTime, humanize } from "@/lib/utils";

const th = "py-2 pr-3 font-medium";
const headRow = "text-left text-[11px] tracking-wide text-muted-foreground uppercase";

export function PredictionKpis() {
  const { data } = usePredictionsSummary();
  return (
    <section aria-label="Prediction metrics" className="grid grid-cols-2 gap-4 lg:grid-cols-4">
      <KpiCard
        label="Vessels with an ETA (24 h)"
        value={data?.eta_vessels ?? null}
        icon={Clock}
        unavailableReason="Loading…"
      />
      <HighRiskKpi />
      <KpiCard
        label="Open vessel anomalies"
        value={data?.open_anomalies ?? null}
        icon={Activity}
        unavailableReason="Loading…"
      />
      <KpiCard
        label="Ports forecast (24 h)"
        value={data?.congestion_ports ?? null}
        icon={Anchor}
        unavailableReason="Loading…"
      />
    </section>
  );
}

function StatusCell({ m }: { m: ModelVersion }) {
  if (m.status === "production") return <Badge tone="success">Production</Badge>;
  if (isRuleModel(m) && m.model_name === "eta")
    return (
      <Badge tone="info" title="Used wherever the trained model is outside its training range">
        Fallback
      </Badge>
    );
  return <Badge>{humanize(m.status)}</Badge>;
}

export function ModelsTable() {
  const { data, error, isPending, refetch } = useModels();
  if (isPending) return <LoadingRows rows={4} />;
  if (error) return <ErrorState error={error} onRetry={() => void refetch()} />;
  // Hide superseded trained versions that never served a prediction; keep everything else.
  const rows = data.filter((m) => m.status !== "archived" || m.predictions > 0 || isRuleModel(m));
  if (rows.length === 0)
    return (
      <EmptyState title="No models registered">
        Run <code className="font-mono">python -m services.ml_jobs train-eta</code> once enough AIS arrivals exist.
      </EmptyState>
    );
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm" aria-label="Model registry">
        <thead>
          <tr className={headRow}>
            <th className={th}>Model</th>
            <th className={th}>Version</th>
            <th className={th}>Status</th>
            <th className={th}>Training data</th>
            <th className={th}>Held-out performance</th>
            <th className="py-2 text-right font-medium">Predictions</th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {rows.map((m) => {
            const h = headlineMetrics(m);
            return (
              <tr key={m.id} data-testid={`model-${m.version}`} className="align-top">
                <td className="py-2.5 pr-3">
                  <p className="font-medium">{MODEL_LABELS[m.model_name] ?? humanize(m.model_name)}</p>
                  <p className="text-[11px] text-muted-foreground">{m.algorithm}</p>
                </td>
                <td className="py-2.5 pr-3">
                  <ModelTag version={m.version} synthetic={m.is_synthetic_trained} />
                </td>
                <td className="py-2.5 pr-3">
                  <StatusCell m={m} />
                </td>
                <td className="py-2.5 pr-3 text-xs">
                  {isRuleModel(m) ? (
                    <span className="text-muted-foreground">Rule-based, no training</span>
                  ) : (
                    <>
                      <p className="tabular-nums">
                        {m.n_train ?? "—"} train / {m.n_test ?? "—"} test
                      </p>
                      {m.training_window_start && m.training_window_end ? (
                        <p className="text-[11px] text-muted-foreground">
                          {formatTime(m.training_window_start)} → {formatTime(m.training_window_end)}
                        </p>
                      ) : null}
                    </>
                  )}
                </td>
                <td className="py-2.5 pr-3 text-xs">
                  {h && h.metrics.length ? (
                    <>
                      <dl className="flex flex-wrap gap-x-3 gap-y-0.5">
                        {h.metrics.map((x) => (
                          <div key={x.label} title={x.hint} className="flex gap-1">
                            <dt className="text-muted-foreground">{x.label}</dt>
                            <dd className="tabular-nums">{x.value}</dd>
                          </div>
                        ))}
                      </dl>
                      <p className="text-[11px] text-muted-foreground">
                        {h.split === "live" ? "Live outcomes" : "Time-based test split"}
                        {h.n != null ? `, n = ${h.n}` : ""}
                      </p>
                    </>
                  ) : (
                    <span className="text-muted-foreground">Not yet evaluated</span>
                  )}
                </td>
                <td className="py-2.5 text-right tabular-nums">{m.predictions.toLocaleString()}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function EtaTable() {
  const { data, error, isPending, refetch } = useEtaPredictions();
  if (isPending) return <LoadingRows rows={5} />;
  if (error) return <ErrorState error={error} onRetry={() => void refetch()} />;
  if (data.length === 0)
    return (
      <EmptyState title="No ETA predictions in the last 24 hours">
        Predictions are made for vessels whose AIS destination resolves to a known port.
      </EmptyState>
    );
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm" aria-label="ETA predictions">
        <thead>
          <tr className={headRow}>
            <th className={th}>Vessel</th>
            <th className={th}>Destination</th>
            <th className={th}>Predicted arrival</th>
            <th className={th}>Range</th>
            <th className="py-2 font-medium">Model</th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {data.map((p) => (
            <tr key={p.id} className="align-top" data-testid="eta-row">
              <td className="py-2 pr-3">
                <Link href={`/vessels/${p.vessel_id}`} className="font-medium hover:underline">
                  {p.vessel_name ?? `MMSI ${p.mmsi}`}
                </Link>
              </td>
              <td className="py-2 pr-3">
                {p.target_port_name ?? "—"}{" "}
                <span className="font-mono text-xs text-muted-foreground">{p.target_unlocode}</span>
              </td>
              <td className="py-2 pr-3 tabular-nums">
                {formatTime(p.predicted_arrival_at)}
                <p className="text-[11px] text-muted-foreground">
                  {formatRelativeHours(p.predicted_arrival_at)} · data {formatAge(p.input_data_as_of)}
                </p>
              </td>
              <td className="py-2 pr-3 text-xs tabular-nums">
                {p.lower_bound_at && p.upper_bound_at ? (
                  <span title={intervalLabel(p) ?? undefined}>
                    {formatRange(p.lower_bound_at, p.upper_bound_at)}
                    <p className="text-[11px] text-muted-foreground">{shortIntervalLabel(p)}</p>
                  </span>
                ) : (
                  "—"
                )}
              </td>
              <td className="py-2">
                <ModelTag version={p.model_version} synthetic={p.is_synthetic_trained} />
                {p.fallback_reason ? (
                  <p
                    className="text-[11px] text-muted-foreground"
                    title={fallbackLabel(p.fallback_reason) ?? undefined}
                  >
                    baseline fallback
                  </p>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function DelayTable() {
  const { data, error, isPending, refetch } = useDelayPredictions();
  if (isPending) return <LoadingRows rows={3} />;
  if (error) return <ErrorState error={error} onRetry={() => void refetch()} />;
  if (data.length === 0)
    return (
      <EmptyState title="No delay predictions">
        A delay model needs observed shipment arrivals to train. With no real arrivals yet, none is in production. A
        model trained on synthetic journeys may be enabled for demos; it only scores synthetic shipments.
      </EmptyState>
    );
  return (
    <ul className="flex flex-col gap-2">
      {data.map((p) => {
        const r = riskLevel(p.delay_probability);
        return (
          <li key={p.id} className="rounded-md border p-3 text-sm" data-testid="delay-row">
            <div className="flex flex-wrap items-center gap-2">
              <Link href={`/shipments/${p.shipment_id}`} className="font-mono font-medium hover:underline">
                {p.reference_value}
              </Link>
              <span className="text-xs text-muted-foreground">
                {p.carrier_name} → {p.destination_unlocode ?? "—"}
              </span>
              {p.shipment_data_mode === "synthetic" ? <Badge tone="synthetic">Synthetic shipment</Badge> : null}
              <span className="ml-auto flex items-center gap-2">
                <span className="tabular-nums font-semibold">{formatProbability(p.delay_probability)}</span>
                <Badge tone={r.tone}>
                  <ShieldAlert className="size-3" aria-hidden />
                  {humanize(r.level)}
                </Badge>
              </span>
            </div>
            {p.factors.length ? (
              <p className="mt-1 text-xs text-muted-foreground">{p.factors.map((f) => f.text).join(" · ")}</p>
            ) : null}
            <div className="mt-1 flex flex-wrap items-center justify-between gap-2 text-[11px] text-muted-foreground">
              <span>
                &gt;{p.threshold_hours} h delay · predicted {formatAge(p.predicted_at)}
              </span>
              <ModelTag version={p.model_version} synthetic={p.is_synthetic_trained} />
            </div>
          </li>
        );
      })}
    </ul>
  );
}

export function CongestionSection() {
  const { data, error, isPending, refetch } = useCongestion();
  const [selected, setSelected] = useState<string | null>(null);
  const portId = selected ?? data?.[0]?.port_id ?? null;
  const detail = usePortCongestion(portId);
  if (isPending) return <LoadingRows rows={5} />;
  if (error) return <ErrorState error={error} onRetry={() => void refetch()} />;
  if (data.length === 0)
    return (
      <EmptyState title="No congestion forecasts yet">
        Forecasts start once hourly port observations exist (derived from AIS port calls).
      </EmptyState>
    );
  const port = data.find((p) => p.port_id === portId) ?? data[0];
  if (!port) return null;
  return (
    <div className="grid gap-4 lg:grid-cols-5">
      <div className="overflow-x-auto lg:col-span-2">
        <table className="w-full text-sm" aria-label="Port congestion">
          <thead>
            <tr className={headRow}>
              <th className={th}>Port</th>
              <th className="py-2 pr-3 text-right font-medium">In port now</th>
              <th className="py-2 text-right font-medium">+24 h</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {data.map((p) => (
              <tr
                key={p.port_id}
                aria-selected={p.port_id === port.port_id}
                className="cursor-pointer hover:bg-muted/50 aria-selected:bg-muted"
                onClick={() => setSelected(p.port_id)}
              >
                <td className="py-1.5 pr-3">
                  <button type="button" className="text-left hover:underline" onClick={() => setSelected(p.port_id)}>
                    {p.name}
                  </button>{" "}
                  <span className="font-mono text-[11px] text-muted-foreground">{p.unlocode}</span>
                </td>
                <td className="py-1.5 pr-3 text-right tabular-nums">{p.current_value ?? "—"}</td>
                <td className="py-1.5 text-right tabular-nums">{p.forecast.at(-1)?.value ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="lg:col-span-3">
        <p className="text-sm font-medium">
          {port.name} <span className="font-mono text-xs text-muted-foreground">{port.unlocode}</span>
        </p>
        <p className="mb-2 text-[11px] text-muted-foreground">
          Vessels in port, hourly. Solid: observed from AIS. Dashed: forecast ({port.method ?? "—"}), made{" "}
          {formatAge(port.predicted_at)}.
        </p>
        {detail.data ? (
          <CongestionChart observed={detail.data.observations.vessels_in_port ?? []} forecast={detail.data.forecast} />
        ) : detail.error ? (
          <ErrorState error={detail.error} />
        ) : (
          <LoadingRows rows={4} />
        )}
        <p className="mt-2 text-[11px] text-muted-foreground">
          {port.history_hours < 24
            ? `Only ${port.history_hours} h of history, so most hours use persistence (last value carried forward); the first 3 h after tracking began are excluded as warm-up. `
            : ""}
          Intervals appear only after 24+ scored forecast errors; none are invented before then. Counts reflect AIS
          coverage, not total port traffic.
        </p>
      </div>
    </div>
  );
}

export function AnomaliesSection() {
  const [status, setStatus] = useState<"open" | "all">("open");
  const { data, error, isPending, refetch } = useAnomalies(null, status);
  return (
    <div className="flex flex-col gap-3">
      <div className="flex gap-1" role="group" aria-label="Anomaly status">
        {(["open", "all"] as const).map((s) => (
          <button
            key={s}
            type="button"
            aria-pressed={status === s}
            onClick={() => setStatus(s)}
            className="rounded-md border px-2 py-0.5 text-xs aria-pressed:bg-muted aria-pressed:font-medium"
          >
            {s === "open" ? "Open" : "All"}
          </button>
        ))}
      </div>
      {isPending ? (
        <LoadingRows rows={3} />
      ) : error ? (
        <ErrorState error={error} onRetry={() => void refetch()} />
      ) : data.length === 0 ? (
        <EmptyState title={status === "open" ? "No open anomalies" : "No anomalies raised"} />
      ) : (
        <ul className="flex flex-col gap-2">
          {data.map((a) => (
            <AnomalyItem key={a.id} a={a} showVessel />
          ))}
        </ul>
      )}
    </div>
  );
}

export function PredictionsView() {
  return (
    <div className="flex flex-col gap-6">
      <PredictionKpis />
      <Card>
        <CardHeader>
          <CardTitle>Models</CardTitle>
          <CardDescription>
            Every model is versioned with its training window and evaluated on a later time period than it was trained
            on. Synthetic-trained models are labeled everywhere their output appears.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <ModelsTable />
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Vessel ETA forecasts</CardTitle>
          <CardDescription>
            Latest prediction per vessel, soonest first. Vessel arrival, not container delivery.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <EtaTable />
        </CardContent>
      </Card>
      <div className="grid gap-6 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Shipment delay risk</CardTitle>
            <CardDescription>Probability of a delay beyond the threshold, highest first.</CardDescription>
          </CardHeader>
          <CardContent>
            <DelayTable />
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Vessel anomalies</CardTitle>
            <CardDescription>
              Rule-based, not learned. AIS-silence alerts are suppressed whenever our own feed had gaps, so an outage is
              never reported as a vessel going dark.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <AnomaliesSection />
          </CardContent>
        </Card>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>Port congestion</CardTitle>
          <CardDescription>Busiest ports by AIS-observed vessels in port, with a 24 hour forecast.</CardDescription>
        </CardHeader>
        <CardContent>
          <CongestionSection />
        </CardContent>
      </Card>
    </div>
  );
}
