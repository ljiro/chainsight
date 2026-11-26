import type { BadgeTone } from "@/components/ui/badge";
import { formatClock, formatTime } from "./utils";
import type { DelayPrediction, EtaPrediction, ModelVersion } from "./types";

/** Same cut-offs the backend uses for risk_assessments.level (high >= 0.7, medium >= 0.4). */
export function riskLevel(p: number): {
  level: "high" | "medium" | "low";
  tone: BadgeTone;
} {
  if (p >= 0.7) return { level: "high", tone: "danger" };
  if (p >= 0.4) return { level: "medium", tone: "warning" };
  return { level: "low", tone: "success" };
}

export function formatProbability(p: number): string {
  if (p < 0.01) return "<1%";
  if (p > 0.99) return ">99%";
  return `${Math.round(p * 100)}%`;
}

/** Plain-language description of how an ETA's range was produced. A heuristic band is never
 *  presented as a calibrated interval. */
export function intervalLabel(e: Pick<EtaPrediction, "interval_method" | "interval_level">): string | null {
  switch (e.interval_method) {
    case "quantile_gbm_p10_p90":
      return `${Math.round((e.interval_level ?? 0.8) * 100)}% interval (quantile model, p10–p90)`;
    case "heuristic_pm20pct":
      return "±20% heuristic band (not a calibrated interval)";
    case null:
    case undefined:
      return null;
    default:
      return e.interval_method;
  }
}

/** Compact form for tables. */
export function shortIntervalLabel(e: Pick<EtaPrediction, "interval_method" | "interval_level">): string {
  if (e.interval_method === "quantile_gbm_p10_p90") return `${Math.round((e.interval_level ?? 0.8) * 100)}% interval`;
  if (e.interval_method === "heuristic_pm20pct") return "±20% heuristic";
  return "method not recorded";
}

const FALLBACKS: Record<string, string> = {
  out_of_training_domain:
    "Outside the trained model's range (distance or horizon), so the distance ÷ speed baseline was used",
  at_training_horizon_limit:
    "The trained model reached the longest horizon in its training data, where it is unreliable, so the distance ÷ speed baseline was used",
  model_artifact_unavailable:
    "The production model's file was not available to this worker, so the distance ÷ speed baseline was used",
};

export function fallbackLabel(reason: string | null): string | null {
  if (!reason) return null;
  return FALLBACKS[reason] ?? reason.replace(/_/g, " ");
}

export function hoursBetween(a: string, b: string): number {
  return (new Date(b).getTime() - new Date(a).getTime()) / 3_600_000;
}

/** "in 3.4 h" / "2.1 h ago" relative to `now`. */
export function formatRelativeHours(iso: string, now: Date = new Date()): string {
  const h = hoursBetween(now.toISOString(), iso);
  const abs = Math.abs(h);
  const v = abs < 10 ? abs.toFixed(1) : Math.round(abs).toString();
  return h >= 0 ? `in ${v} h` : `${v} h ago`;
}

export const MODEL_LABELS: Record<string, string> = {
  eta: "Vessel ETA",
  delay: "Shipment delay",
  congestion: "Port congestion",
};

export function isRuleModel(m: Pick<ModelVersion, "feature_schema">): boolean {
  return m.feature_schema?.type === "rule";
}

type Metric = { label: string; value: string; hint?: string };

function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

/** The headline metrics worth showing for a model's latest held-out evaluation. */
export function headlineMetrics(m: ModelVersion): { split: string; n: number | null; metrics: Metric[] } | null {
  const ev =
    m.evaluations.find((e) => e.split === "test") ?? m.evaluations.find((e) => e.split === "live") ?? m.evaluations[0];
  if (!ev) return null;
  const x = ev.metrics;
  const out: Metric[] = [];
  const add = (label: string, v: number | null, fmt: (n: number) => string, hint?: string) => {
    if (v != null) out.push({ label, value: fmt(v), hint });
  };
  if (m.model_name === "eta") {
    add("MAE", num(x.mae_hours), (v) => `${v.toFixed(2)} h`, "Mean absolute error of the arrival time");
    add("Median AE", num(x.median_ae_hours), (v) => `${v.toFixed(2)} h`);
    add(
      "Bias",
      num(x.bias_hours),
      (v) => `${v > 0 ? "+" : ""}${v.toFixed(2)} h`,
      "Positive = predicted later than actual",
    );
    add(
      "Interval coverage",
      num(x.interval_coverage),
      (v) => `${Math.round(v * 100)}%`,
      "Share of actual arrivals inside the predicted interval",
    );
  } else if (m.model_name === "delay") {
    add("PR-AUC", num(x.pr_auc), (v) => v.toFixed(3));
    add("Brier", num(x.brier), (v) => v.toFixed(3), "Lower is better; measures probability accuracy");
    add("ECE", num(x.ece), (v) => v.toFixed(3), "Expected calibration error");
    add("Recall", num(x.recall), (v) => `${Math.round(v * 100)}%`);
  } else {
    add("MAE", num(x.mae_vessels), (v) => `${v.toFixed(2)} vessels`, "Mean absolute error of vessels in port");
  }
  return { split: ev.split, n: ev.n_samples, metrics: out };
}

export function sortDelay(items: DelayPrediction[]): DelayPrediction[] {
  return [...items].sort((a, b) => b.delay_probability - a.delay_probability);
}

/** "13:10–15:40 UTC" on one day; full date-times when the range crosses midnight (UTC). */
export function formatRange(lower: string, upper: string): string {
  if (lower.slice(0, 10) === upper.slice(0, 10) && new Date(lower).getUTCDate() === new Date(upper).getUTCDate())
    return `${formatClock(lower)}–${formatClock(upper)} UTC`;
  return `${formatTime(lower)} – ${formatTime(upper)}`;
}
