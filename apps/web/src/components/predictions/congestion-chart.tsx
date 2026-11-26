"use client";

import { Area, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { ForecastPoint } from "@/lib/types";
import { formatClock, formatTime } from "@/lib/utils";

type Row = {
  t: number;
  observed?: number;
  forecast?: number;
  band?: [number, number];
};

/** Merge hourly observations and the forecast into one time axis. The forecast is drawn
 *  dashed and starts from the last observation, so the hand-off is visible. */
export function congestionRows(observed: { t: string; value: number }[], forecast: ForecastPoint[]): Row[] {
  const rows = new Map<number, Row>();
  for (const o of observed)
    rows.set(new Date(o.t).getTime(), {
      t: new Date(o.t).getTime(),
      observed: o.value,
    });
  const last = observed.at(-1);
  if (last && forecast.length) {
    const r = rows.get(new Date(last.t).getTime());
    if (r) r.forecast = last.value;
  }
  for (const f of forecast) {
    const t = new Date(f.target_time).getTime();
    const r = rows.get(t) ?? { t };
    r.forecast = f.value;
    if (f.lower != null && f.upper != null) r.band = [f.lower, f.upper];
    rows.set(t, r);
  }
  return [...rows.values()].sort((a, b) => a.t - b.t);
}

const fmtHour = (t: number) => formatTime(new Date(t).toISOString());

function ChartTooltip({ active, payload, label }: { active?: boolean; payload?: { payload: Row }[]; label?: number }) {
  const r = payload?.[0]?.payload;
  if (!active || !r || label == null) return null;
  return (
    <div className="rounded-md border bg-popover px-2.5 py-1.5 text-xs shadow-sm">
      <p className="text-muted-foreground">{fmtHour(label)}</p>
      {r.observed != null ? <p className="tabular-nums">Observed: {r.observed} vessels</p> : null}
      {r.forecast != null && r.observed == null ? <p className="tabular-nums">Forecast: {r.forecast} vessels</p> : null}
      {r.band ? (
        <p className="text-muted-foreground tabular-nums">
          Interval: {r.band[0]}–{r.band[1]}
        </p>
      ) : null}
    </div>
  );
}

export function CongestionChart({
  observed,
  forecast,
}: {
  observed: { t: string; value: number }[];
  forecast: ForecastPoint[];
}) {
  const rows = congestionRows(observed, forecast);
  const hasBand = rows.some((r) => r.band);
  return (
    <div className="h-56 w-full" role="img" aria-label="Vessels in port (UTC): observed history and 24 hour forecast">
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={rows} margin={{ top: 8, right: 12, bottom: 0, left: -12 }}>
          <CartesianGrid stroke="var(--border)" strokeDasharray="0" vertical={false} />
          <XAxis
            dataKey="t"
            type="number"
            scale="time"
            domain={["dataMin", "dataMax"]}
            tickFormatter={(t: number) => formatClock(new Date(t).toISOString())}
            tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
            stroke="var(--border)"
            minTickGap={32}
          />
          <YAxis
            allowDecimals={false}
            tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
            stroke="var(--border)"
            width={44}
          />
          <Tooltip content={<ChartTooltip />} cursor={{ stroke: "var(--muted-foreground)", strokeWidth: 1 }} />
          {hasBand ? (
            <Area dataKey="band" stroke="none" fill="var(--chart-1)" fillOpacity={0.12} isAnimationActive={false} />
          ) : null}
          <Line
            dataKey="observed"
            stroke="var(--chart-1)"
            strokeWidth={2}
            dot={false}
            connectNulls
            isAnimationActive={false}
          />
          <Line
            dataKey="forecast"
            stroke="var(--chart-1)"
            strokeWidth={2}
            strokeDasharray="5 4"
            dot={false}
            connectNulls
            isAnimationActive={false}
          />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
