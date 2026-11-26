import type { BacktestRunSummary, MetricCell, ResearchIndicator } from "./types";

export const FREQUENCY_LABELS: Record<string, string> = { D: "Daily", W: "Weekly (Fri)", M: "Monthly" };

export const TRANSFORM_LABELS: Record<string, string> = {
  level: "Level",
  yoy: "Year-over-year change",
  ma4: "4-period moving average",
  ma13: "13-period moving average",
  pct1: "Change vs previous period",
  pct4: "Change over 4 periods",
  z26: "26-period z-score",
  z52: "52-period z-score",
  diff1: "Difference vs previous period",
};

export function featureKey(base: string, frequency: string, transform: string): string {
  return `${base}|${frequency}|${transform}`;
}

export function parseFeatureKey(key: string): { base: string; frequency: string; transform: string } | null {
  const parts = key.split("|");
  if (parts.length !== 3) return null;
  const [base, frequency, transform] = parts as [string, string, string];
  return { base, frequency, transform };
}

/** Plain-language availability rule, e.g. "Weekly release (Tue 12:00 UTC), ≥4 days after the day, +7 days safety". */
export function describeRule(rule: Record<string, unknown> | undefined): string {
  if (!rule) return "—";
  if (rule.kind === "weekly_release") {
    const days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
    const wd = days[Number(rule.weekday)] ?? "?";
    return `Weekly release (${wd} ${String(rule.hour).padStart(2, "0")}:00 UTC), ≥${rule.min_lag_days} days after the day, +${rule.safety_days} days safety`;
  }
  if (rule.kind === "next_day") {
    const extra = Number(rule.lag_days ?? 0);
    return extra ? `Next day + ${extra} days` : "Next day 00:00 UTC (closing price)";
  }
  return String(rule.kind);
}

export function indicatorGroup(i: ResearchIndicator): string {
  if (i.kind === "composite") return "Composites";
  if (i.category === "market") return "Market prices";
  if (i.entity_kind === "chokepoint") return "Chokepoints (PortWatch)";
  if (i.entity_kind === "port") return "Ports (PortWatch)";
  return "Other";
}

export function formatNumber(v: number | null | undefined, digits = 2): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toLocaleString("en-US", { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

export function formatPct(v: number | null | undefined, digits = 1): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return `${(v * 100).toFixed(digits)}%`;
}

/** p-values: never "0", and small ones shown as a bound. */
export function formatP(p: number | null | undefined): string {
  if (p == null || !Number.isFinite(p)) return "—";
  if (p < 0.001) return "<0.001";
  return p.toFixed(3);
}

type MetricDef = { key: string; label: string; format: (v: number | null) => string; hint?: string };

export const METRICS: MetricDef[] = [
  {
    key: "sharpe",
    label: "Sharpe (ann.)",
    format: (v) => formatNumber(v),
    hint: "Mean / volatility of net period returns, annualized; no risk-free rate",
  },
  {
    key: "sharpe_t",
    label: "Sharpe t-stat",
    format: (v) => formatNumber(v),
    hint: "Sharpe / Lo (2002) standard error; |t| < 2 is indistinguishable from zero",
  },
  { key: "total_return", label: "Total return", format: (v) => formatPct(v) },
  { key: "cagr", label: "CAGR", format: (v) => formatPct(v) },
  { key: "ann_vol", label: "Volatility (ann.)", format: (v) => formatPct(v) },
  { key: "max_drawdown", label: "Max drawdown", format: (v) => formatPct(v) },
  { key: "sortino", label: "Sortino", format: (v) => formatNumber(v) },
  {
    key: "hit_rate",
    label: "Hit rate",
    format: (v) => formatPct(v),
    hint: "Share of invested periods with a positive net return",
  },
  { key: "exposure", label: "Avg. exposure", format: (v) => formatPct(v, 0) },
  {
    key: "turnover_ann",
    label: "Turnover (ann.)",
    format: (v) => formatNumber(v, 1),
    hint: "Sum of absolute position changes per year",
  },
  {
    key: "information_ratio",
    label: "Information ratio",
    format: (v) => formatNumber(v),
    hint: "Active return vs buy-and-hold / tracking error",
  },
  { key: "n_periods", label: "Periods", format: (v) => (v == null ? "—" : String(Math.round(v))) },
];

export function metricCell(
  metrics: Record<string, MetricCell> | null | undefined,
  split: string,
  key: string,
): MetricCell {
  return metrics?.[`${split}.${key}`] ?? { value: null, benchmark: null };
}

/** Out-of-sample verdict in words. Deliberately conservative. */
export function oosVerdict(run: Pick<BacktestRunSummary, "metrics" | "status"> | null | undefined): {
  label: string;
  tone: "neutral" | "warning" | "success" | "danger";
} {
  if (!run || run.status !== "succeeded")
    return { label: run?.status === "failed" ? "Run failed" : "Not run", tone: "danger" };
  const s = metricCell(run.metrics, "out_of_sample", "sharpe");
  const t = metricCell(run.metrics, "out_of_sample", "sharpe_t").value;
  if (s.value == null) return { label: "No result", tone: "neutral" };
  if (t != null && Math.abs(t) < 2) return { label: "No significant edge", tone: "neutral" };
  if (s.benchmark != null && s.value < s.benchmark) return { label: "Below buy-and-hold", tone: "warning" };
  return { label: "Beats benchmark OOS (verify)", tone: "success" };
}

/** "26 Nov 2019" (UTC). */
export function formatDay(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });
}
