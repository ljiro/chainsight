"use client";

import { useMemo, useState, type ReactNode } from "react";
import { LagChart, ScatterPlot, TimeSeriesChart } from "@/components/research/charts";
import { EmptyState, ErrorState, LoadingRows } from "@/components/query-state";
import { Badge } from "@/components/ui/badge";
import { useAnalysis, useFeatureSeries, useMarketSeries, useResearchIndicators } from "@/lib/queries";
import {
  FREQUENCY_LABELS,
  TRANSFORM_LABELS,
  describeRule,
  featureKey,
  formatDay,
  formatNumber,
  formatP,
  formatPct,
  indicatorGroup,
} from "@/lib/research";
import type { Analysis, ResearchIndicator, Section, Stationarity } from "@/lib/types";
import { humanize } from "@/lib/utils";

const DEFAULT_BASE = "composite:hormuz_bab_tanker";
const DEFAULT_ASSET = "fred:DCOILBRENTEU";

export type Selection = { base: string; frequency: string; transform: string; asset: string };

const select = "h-8 rounded-md border bg-background px-2 text-sm";

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="flex min-w-0 flex-col gap-1 text-xs text-muted-foreground">
      {label}
      {children}
    </label>
  );
}

export function IndicatorPicker({
  indicators,
  value,
  onChange,
}: {
  indicators: ResearchIndicator[];
  value: string;
  onChange: (key: string) => void;
}) {
  const groups = useMemo(() => {
    const g = new Map<string, ResearchIndicator[]>();
    for (const i of indicators.filter((i) => i.category === "maritime" && i.available !== false)) {
      const k = indicatorGroup(i);
      g.set(k, [...(g.get(k) ?? []), i]);
    }
    return [...g.entries()];
  }, [indicators]);
  return (
    <select
      aria-label="Indicator"
      className={`${select} w-full`}
      value={value}
      onChange={(e) => onChange(e.target.value)}
    >
      {groups.map(([group, items]) => (
        <optgroup key={group} label={group}>
          {items.map((i) => (
            <option key={i.key} value={i.key}>
              {i.name}
            </option>
          ))}
        </optgroup>
      ))}
    </select>
  );
}

export function useSelection(): [Selection, (s: Partial<Selection>) => void] {
  const [sel, setSel] = useState<Selection>({
    base: DEFAULT_BASE,
    frequency: "W",
    transform: "yoy",
    asset: DEFAULT_ASSET,
  });
  return [sel, (s) => setSel((prev) => ({ ...prev, ...s }))];
}

export function Explorer({ sel, onChange }: { sel: Selection; onChange: (s: Partial<Selection>) => void }) {
  const catalog = useResearchIndicators();
  const key = featureKey(sel.base, sel.frequency, sel.transform);
  const feature = useFeatureSeries(key);
  const market = useMarketSeries(sel.asset);
  const indicator = catalog.data?.indicators.find((i) => i.key === sel.base);
  const assets = catalog.data?.indicators.filter((i) => i.category === "market") ?? [];

  if (catalog.isPending) return <LoadingRows rows={4} />;
  if (catalog.error) return <ErrorState error={catalog.error} onRetry={() => void catalog.refetch()} />;
  if (!catalog.data.indicators.some((i) => i.category === "maritime" && i.kind === "series"))
    return (
      <EmptyState title="No research data collected yet">
        Run <code className="font-mono">python -m services.research_jobs collect</code> (IMF PortWatch and FRED; no keys
        needed).
      </EmptyState>
    );

  return (
    <div className="flex flex-col gap-4">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-[2fr_1fr_1.4fr_1.4fr]">
        <Field label="Maritime indicator">
          <IndicatorPicker indicators={catalog.data.indicators} value={sel.base} onChange={(base) => onChange({ base })} />
        </Field>
        <Field label="Frequency">
          <select aria-label="Frequency" className={select} value={sel.frequency} onChange={(e) => onChange({ frequency: e.target.value })}>
            {Object.entries(FREQUENCY_LABELS).map(([k, v]) => (
              <option key={k} value={k}>{v}</option>
            ))}
          </select>
        </Field>
        <Field label="Transform">
          <select aria-label="Transform" className={select} value={sel.transform} onChange={(e) => onChange({ transform: e.target.value })}>
            {catalog.data.transforms.map((t) => (
              <option key={t} value={t}>{TRANSFORM_LABELS[t] ?? t}</option>
            ))}
          </select>
        </Field>
        <Field label="Compare against">
          <select aria-label="Asset" className={select} value={sel.asset} onChange={(e) => onChange({ asset: e.target.value })}>
            {assets.map((a) => (
              <option key={a.key} value={a.key}>{a.name}</option>
            ))}
          </select>
        </Field>
      </div>

      {indicator ? <IndicatorProvenance i={indicator} /> : null}

      <div className="grid gap-4 xl:grid-cols-2">
        <div>
          <p className="text-sm font-medium">{indicator?.name ?? sel.base}</p>
          <p className="mb-1 text-[11px] text-muted-foreground">
            {FREQUENCY_LABELS[sel.frequency]} · {TRANSFORM_LABELS[sel.transform] ?? sel.transform}
            {feature.data ? ` · ${feature.data.n} periods · median ${feature.data.median_availability_lag_days} days from period end to availability` : ""}
          </p>
          {feature.error ? (
            <ErrorState error={feature.error} />
          ) : feature.data ? (
            <TimeSeriesChart label={`${feature.data.name} over time`}
              points={feature.data.points.map((p) => ({ t: Date.parse(p.as_of), value: p.value }))} />
          ) : (
            <LoadingRows rows={4} />
          )}
        </div>
        <div>
          <p className="text-sm font-medium">{market.data?.name ?? sel.asset}</p>
          <p className="mb-1 text-[11px] text-muted-foreground">
            Daily close{market.data?.unit ? `, ${market.data.unit}` : ""} · FRED
          </p>
          {market.error ? (
            <ErrorState error={market.error} />
          ) : market.data ? (
            <TimeSeriesChart label={`${market.data.name} over time`} color="var(--chart-2)"
              points={market.data.points.map((p) => ({ t: Date.parse(p.as_of), value: p.value }))} />
          ) : (
            <LoadingRows rows={4} />
          )}
        </div>
      </div>

      <AnalysisPanel feature={key} asset={sel.asset} featureName={indicator?.name ?? sel.base} />
    </div>
  ); // prettier-ignore
}

function IndicatorProvenance({ i }: { i: ResearchIndicator }) {
  if (i.kind === "composite")
    return (
      <p className="text-[11px] text-muted-foreground">
        {i.description} Inputs: {i.inputs?.length} PortWatch series. A day counts only when every input has a value.
      </p>
    );
  return (
    <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-muted-foreground">
      <Badge>{i.source_name}</Badge>
      {i.attributes?.estimated ? <Badge tone="info">IMF estimate</Badge> : null}
      <span>
        {formatDay(i.first_as_of)} → {formatDay(i.last_as_of)} · {i.n_obs} obs
        {i.n_revisions ? ` · ${i.n_revisions} revisions` : ""}
      </span>
      <span>Availability: {describeRule(i.attributes?.availability_rule)}</span>
    </p>
  );
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-md border p-3" title={hint}>
      <p className="text-[11px] text-muted-foreground">{label}</p>
      <p className="mt-0.5 text-lg font-semibold tabular-nums">{value}</p>
    </div>
  );
}

function Unavailable({ s }: { s: { ok: false; reason: string } }) {
  return <p className="text-xs text-muted-foreground">Not computed: {s.reason}</p>;
}

function StationarityRow({ name, s }: { name: string; s: Section<Stationarity> }) {
  return (
    <tr>
      <td className="py-1.5 pr-3">{name}</td>
      {s.ok ? (
        <>
          <td className="py-1.5 pr-3 tabular-nums">{formatP(s.result.adf_p)}</td>
          <td className="py-1.5 pr-3 tabular-nums">{formatP(s.result.kpss_p)}</td>
          <td className="py-1.5">
            <Badge tone={s.result.verdict === "stationary" ? "success" : s.result.verdict === "non_stationary" ? "warning" : "neutral"}>
              {humanize(s.result.verdict)}
            </Badge>
          </td>
        </>
      ) : (
        <td colSpan={3} className="py-1.5 text-xs text-muted-foreground">{s.reason}</td>
      )}
    </tr>
  ); // prettier-ignore
}

export function AnalysisPanel({
  feature,
  asset,
  featureName,
}: {
  feature: string;
  asset: string;
  featureName: string;
}) {
  const [lag, setLag] = useState(0);
  const { data, error, isPending, isFetching } = useAnalysis(feature, asset, lag);
  return (
    <section aria-label="Statistical analysis" className="flex flex-col gap-4 border-t pt-4">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold">Does the feature lead the asset&apos;s returns?</h3>
          <p className="text-[11px] text-muted-foreground">
            Point-in-time: each feature value is matched with the return that starts after it was available.
          </p>
        </div>
        <Field label="Extra execution delay">
          <select aria-label="Execution delay" className={select} value={lag} onChange={(e) => setLag(Number(e.target.value))}>
            {[0, 1, 2, 5].map((d) => (
              <option key={d} value={d}>{d === 0 ? "None (first close after decision)" : `${d} day(s)`}</option>
            ))}
          </select>
        </Field>
      </div>
      {isPending ? (
        <LoadingRows rows={5} />
      ) : error ? (
        <ErrorState error={error} />
      ) : (
        <div className={isFetching ? "opacity-60 transition-opacity" : undefined}>
          <AnalysisResults a={data} featureName={featureName} />
        </div>
      )}
    </section>
  ); // prettier-ignore
}

function AnalysisResults({ a, featureName }: { a: Analysis; featureName: string }) {
  const c = a.correlation.ok ? a.correlation.result : null;
  const oos = a.out_of_sample.ok ? a.out_of_sample.result : null;
  const reg = a.regression.ok ? a.regression.result : null;
  return (
    <div className="flex flex-col gap-4" data-testid="analysis">
      <p className="text-[11px] text-muted-foreground">
        {a.alignment.n} periods, {formatDay(a.alignment.first)} → {formatDay(a.alignment.last)}.
        Median feature age at decision: {a.alignment.median_staleness_days} days. {a.alignment.note}
      </p>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Pearson r (next period)" value={formatNumber(c?.pearson ?? null, 3)} hint={`p = ${formatP(c?.pearson_p)}`} />
        <Stat label="p-value" value={formatP(c?.pearson_p)} hint="Two-sided; not adjusted for the many features and lags tried" />
        <Stat label="Out-of-sample R²" value={formatPct(oos?.r2_oos ?? null, 2)}
              hint="Expanding-window regression vs the historical-mean forecast; below 0 = worse than the mean" />
        <Stat label="Direction hit rate (OOS)" value={formatPct(oos?.hit_rate ?? null)}
              hint={`Historical-mean forecast: ${formatPct(oos?.hit_rate_mean_forecast ?? null)}`} />
      </div>
      <div className="grid gap-4 xl:grid-cols-2">
        <div>
          <p className="text-xs font-medium">Correlation by lag</p>
          <p className="mb-1 text-[11px] text-muted-foreground">
            Blue: feature leads returns (lag 0 = the next period). Gray: returns lead the feature. Dashed: ±1.96/√n,
            what noise alone produces 95% of the time.
          </p>
          {a.lagged_correlation.ok ? <LagChart points={a.lagged_correlation.result} /> : <Unavailable s={a.lagged_correlation} />}
        </div>
        <div>
          <p className="text-xs font-medium">Next-period return vs feature</p>
          <p className="mb-1 text-[11px] text-muted-foreground">Each dot is one decision date.</p>
          <ScatterPlot points={a.scatter} xLabel={featureName} />
        </div>
      </div>
      <div className="grid gap-4 xl:grid-cols-2">
        <div>
          <p className="text-xs font-medium">Rolling 52-period correlation</p>
          <p className="mb-1 text-[11px] text-muted-foreground">A relationship that flips sign over time is not a stable one.</p>
          {a.rolling_correlation.ok ? (
            <TimeSeriesChart height={180} label="Rolling correlation between the feature and next-period returns"
              points={a.rolling_correlation.result.map((p) => ({ t: Date.parse(p.t), value: p.value }))}
              format={(v) => v.toFixed(2)} />
          ) : <Unavailable s={a.rolling_correlation} />}
        </div>
        <div className="flex flex-col gap-3 text-sm">
          <table className="w-full text-sm" aria-label="Stationarity tests">
            <thead>
              <tr className="text-left text-[11px] tracking-wide text-muted-foreground uppercase">
                <th className="py-1 pr-3 font-medium">Series</th>
                <th className="py-1 pr-3 font-medium" title="Augmented Dickey-Fuller, H0: unit root">ADF p</th>
                <th className="py-1 pr-3 font-medium" title="KPSS, H0: stationary">KPSS p</th>
                <th className="py-1 font-medium">Verdict</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              <StationarityRow name="Feature" s={a.stationarity.feature} />
              <StationarityRow name="Returns" s={a.stationarity.returns} />
            </tbody>
          </table>
          <div>
            <p className="text-xs font-medium">Regression: return = α + β·feature (Newey-West errors)</p>
            {reg ? (
              <p className="text-xs tabular-nums text-muted-foreground">
                β = {formatNumber(reg.beta, 4)} · t = {formatNumber(reg.beta_t)} · p = {formatP(reg.beta_p)} · R² ={" "}
                {formatNumber(reg.r2, 4)} · {reg.hac_lags} HAC lags
              </p>
            ) : a.regression.ok ? null : <Unavailable s={a.regression} />}
          </div>
          <div>
            <p className="text-xs font-medium">Granger test: do past feature values help forecast returns?</p>
            {a.granger.ok ? (
              <p className="text-xs tabular-nums text-muted-foreground">
                {a.granger.result.map((g) => `lag ${g.lag}: p = ${formatP(g.p)}`).join(" · ")}
              </p>
            ) : <Unavailable s={a.granger} />}
            <p className="text-[11px] text-muted-foreground">Needs stationary series; predictive association, not causation.</p>
          </div>
        </div>
      </div>
      <p className="rounded-md border border-warning/30 bg-warning/5 p-2 text-[11px]">{a.disclaimer}</p>
    </div>
  ); // prettier-ignore
}
