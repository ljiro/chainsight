"use client";

import type { ReactNode } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  Area,
  AreaChart,
} from "recharts";

const AXIS = { fontSize: 11, fill: "var(--muted-foreground)" };
const GRID = <CartesianGrid stroke="var(--border)" vertical={false} />;

export const dateTick = (t: number) =>
  new Date(t).toLocaleDateString("en-GB", { month: "short", year: "2-digit", timeZone: "UTC" });
const dateFull = (t: number) =>
  new Date(t).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric", timeZone: "UTC" });

function Frame({ label, height = 220, children }: { label: string; height?: number; children: ReactNode }) {
  return (
    <div role="img" aria-label={label} style={{ height }} className="w-full">
      <ResponsiveContainer width="100%" height="100%">
        {children as React.ReactElement}
      </ResponsiveContainer>
    </div>
  );
}

function TipBox({ children }: { children: ReactNode }) {
  return <div className="rounded-md border bg-popover px-2.5 py-1.5 text-xs shadow-sm">{children}</div>;
}

type TipProps<T> = { active?: boolean; payload?: { payload: T }[] };

export type TimePoint = { t: number; value: number | null };

/** One series over time. */
export function TimeSeriesChart({
  points,
  label,
  color = "var(--chart-1)",
  format = (v: number) => v.toLocaleString("en-US", { maximumFractionDigits: 3 }),
  height,
}: {
  points: TimePoint[];
  label: string;
  color?: string;
  format?: (v: number) => string;
  height?: number;
}) {
  const Tip = ({ active, payload }: TipProps<TimePoint>) => {
    const p = payload?.[0]?.payload;
    if (!active || !p || p.value == null) return null;
    return (
      <TipBox>
        <p className="text-muted-foreground">{dateFull(p.t)}</p>
        <p className="tabular-nums">{format(p.value)}</p>
      </TipBox>
    );
  };
  return (
    <Frame label={label} height={height}>
      <LineChart data={points} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
        {GRID}
        <XAxis dataKey="t" type="number" scale="time" domain={["dataMin", "dataMax"]} tickFormatter={dateTick}
               tick={AXIS} stroke="var(--border)" minTickGap={40} />
        <YAxis tick={AXIS} stroke="var(--border)" width={56} tickFormatter={(v: number) => format(v)} domain={["auto", "auto"]} />
        <Tooltip content={<Tip />} cursor={{ stroke: "var(--muted-foreground)", strokeWidth: 1 }} />
        <Line dataKey="value" stroke={color} strokeWidth={2} dot={false} isAnimationActive={false} connectNulls={false} />
      </LineChart>
    </Frame>
  ); // prettier-ignore
}

export type LagPoint = { lag: number; corr: number | null; ci95: number | null; p: number | null; n: number };

/** corr(x_t, y_{t+k}) by lag, with the ±1.96/√n band of what noise alone produces. */
export function LagChart({ points }: { points: LagPoint[] }) {
  const ci = Math.max(...points.map((p) => p.ci95 ?? 0));
  // Symmetric, round axis that always shows the noise band.
  const peak = Math.max(ci, ...points.map((p) => Math.abs(p.corr ?? 0)));
  const bound = Math.max(0.1, Math.ceil(peak * 1.15 * 10) / 10);
  const Tip = ({ active, payload }: TipProps<LagPoint>) => {
    const p = payload?.[0]?.payload;
    if (!active || !p) return null;
    return (
      <TipBox>
        <p className="text-muted-foreground">
          Lag {p.lag} {p.lag >= 0 ? "(feature leads)" : "(returns lead; not tradeable)"}
        </p>
        <p className="tabular-nums">
          r = {p.corr?.toFixed(3)} · p = {p.p?.toFixed(3)} · n = {p.n}
        </p>
      </TipBox>
    );
  };
  return (
    <Frame label="Correlation between the feature and later returns, by lag" height={200}>
      <BarChart data={points} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
        {GRID}
        <XAxis dataKey="lag" tick={AXIS} stroke="var(--border)" />
        <YAxis tick={AXIS} stroke="var(--border)" width={44} domain={[-bound, bound]}
               ticks={[-bound, -bound / 2, 0, bound / 2, bound]} tickFormatter={(v: number) => v.toFixed(2)} />
        <ReferenceLine y={ci} stroke="var(--muted-foreground)" strokeDasharray="4 3" />
        <ReferenceLine y={-ci} stroke="var(--muted-foreground)" strokeDasharray="4 3" />
        <ReferenceLine y={0} stroke="var(--border)" />
        <Tooltip content={<Tip />} cursor={{ fill: "var(--muted)", opacity: 0.4 }} />
        <Bar dataKey="corr" radius={[4, 4, 0, 0]} isAnimationActive={false}>
          {points.map((p) => (
            <Cell key={p.lag} fill={p.lag >= 0 ? "var(--chart-1)" : "var(--chart-muted)"} />
          ))}
        </Bar>
      </BarChart>
    </Frame>
  ); // prettier-ignore
}

export type ScatterPoint = { x: number; y: number; t: string };

export function ScatterPlot({ points, xLabel }: { points: ScatterPoint[]; xLabel: string }) {
  const Tip = ({ active, payload }: TipProps<ScatterPoint>) => {
    const p = payload?.[0]?.payload;
    if (!active || !p) return null;
    return (
      <TipBox>
        <p className="text-muted-foreground">{dateFull(Date.parse(p.t))}</p>
        <p className="tabular-nums">
          feature {p.x.toFixed(3)} · next return {(p.y * 100).toFixed(2)}%
        </p>
      </TipBox>
    );
  };
  return (
    <Frame label={`Next-period return against ${xLabel}`} height={220}>
      <ScatterChart margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
        <CartesianGrid stroke="var(--border)" />
        <XAxis dataKey="x" type="number" tick={AXIS} stroke="var(--border)" domain={["auto", "auto"]}
               tickFormatter={(v: number) => v.toFixed(1)} />
        <YAxis dataKey="y" type="number" tick={AXIS} stroke="var(--border)" width={48}
               tickFormatter={(v: number) => `${(v * 100).toFixed(0)}%`} />
        <Tooltip content={<Tip />} />
        <Scatter data={points} fill="var(--chart-1)" fillOpacity={0.55} isAnimationActive={false} />
      </ScatterChart>
    </Frame>
  ); // prettier-ignore
}

export type EquityPoint = { t: number; equity: number; benchmark_equity: number; drawdown: number; position: number };

/** Strategy vs buy-and-hold growth of 1, out of sample only. */
export function EquityChart({ points }: { points: EquityPoint[] }) {
  const Tip = ({ active, payload }: TipProps<EquityPoint>) => {
    const p = payload?.[0]?.payload;
    if (!active || !p) return null;
    return (
      <TipBox>
        <p className="text-muted-foreground">{dateFull(p.t)}</p>
        <p className="tabular-nums">
          Strategy: {p.equity.toFixed(3)} (position {p.position})
        </p>
        <p className="tabular-nums">Buy and hold: {p.benchmark_equity.toFixed(3)}</p>
      </TipBox>
    );
  };
  return (
    <div>
      <div className="mb-1 flex gap-4 text-[11px] text-muted-foreground" aria-hidden>
        <span className="flex items-center gap-1.5">
          <span className="h-0.5 w-4 rounded" style={{ background: "var(--chart-1)" }} /> Strategy (net of costs)
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-0.5 w-4 rounded" style={{ background: "var(--chart-muted)" }} /> Buy and hold
        </span>
      </div>
      <Frame label="Out-of-sample growth of 1: strategy vs buy and hold" height={240}>
        <LineChart data={points} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
          {GRID}
          <XAxis dataKey="t" type="number" scale="time" domain={["dataMin", "dataMax"]} tickFormatter={dateTick}
                 tick={AXIS} stroke="var(--border)" minTickGap={40} />
          <YAxis tick={AXIS} stroke="var(--border)" width={44} domain={["auto", "auto"]} tickFormatter={(v: number) => v.toFixed(1)} />
          <ReferenceLine y={1} stroke="var(--border)" />
          <Tooltip content={<Tip />} cursor={{ stroke: "var(--muted-foreground)", strokeWidth: 1 }} />
          <Line dataKey="benchmark_equity" stroke="var(--chart-muted)" strokeWidth={2} dot={false} isAnimationActive={false} />
          <Line dataKey="equity" stroke="var(--chart-1)" strokeWidth={2} dot={false} isAnimationActive={false} />
        </LineChart>
      </Frame>
    </div>
  ); // prettier-ignore
}

export function DrawdownChart({ points }: { points: EquityPoint[] }) {
  return (
    <Frame label="Out-of-sample drawdown of the strategy" height={140}>
      <AreaChart data={points} margin={{ top: 4, right: 12, bottom: 0, left: 0 }}>
        {GRID}
        <XAxis dataKey="t" type="number" scale="time" domain={["dataMin", "dataMax"]} tickFormatter={dateTick}
               tick={AXIS} stroke="var(--border)" minTickGap={40} />
        <YAxis tick={AXIS} stroke="var(--border)" width={44} tickFormatter={(v: number) => `${Math.round(v * 100)}%`} />
        <Tooltip formatter={(v: number) => `${(v * 100).toFixed(1)}%`} labelFormatter={(t: number) => dateFull(t)} />
        <Area dataKey="drawdown" stroke="var(--chart-1)" strokeWidth={2} fill="var(--chart-1)" fillOpacity={0.15}
              isAnimationActive={false} name="Drawdown" />
      </AreaChart>
    </Frame>
  ); // prettier-ignore
}
