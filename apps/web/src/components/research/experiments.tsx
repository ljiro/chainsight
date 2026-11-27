"use client";

import { FlaskConical, Loader2, RotateCw } from "lucide-react";
import { useState, type FormEvent } from "react";
import { DrawdownChart, EquityChart } from "@/components/research/charts";
import type { Selection } from "@/components/research/explorer";
import { EmptyState, ErrorState, LoadingRows } from "@/components/query-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  useCreateExperiment,
  useExperiment,
  useExperiments,
  useMultipleTesting,
  useRerunExperiment,
} from "@/lib/queries";
import { METRICS, featureKey, formatDay, formatNumber, formatP, metricCell, oosVerdict } from "@/lib/research";
import type { BacktestRun, Experiment } from "@/lib/types";
import { formatAge } from "@/lib/utils";

const th = "py-2 pr-3 font-medium";
const headRow = "text-left text-[11px] tracking-wide text-muted-foreground uppercase";

export function ExperimentsTable({ selected, onSelect }: { selected: string | null; onSelect: (id: string) => void }) {
  const { data, error, isPending, refetch } = useExperiments();
  const mt = useMultipleTesting();
  const deflated = new Map((mt.data?.experiments ?? []).map((d) => [d.experiment_id, d]));
  if (isPending) return <LoadingRows rows={3} />;
  if (error) return <ErrorState error={error} onRetry={() => void refetch()} />;
  if (data.length === 0)
    return (
      <EmptyState title="No experiments yet">
        Seed the examples with <code className="font-mono">python -m services.research_jobs seed-experiments</code>, or
        create one below.
      </EmptyState>
    );
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm" aria-label="Research experiments">
        <thead>
          <tr className={headRow}>
            <th className={th}>Experiment</th>
            <th className={th}>Asset</th>
            <th className="py-2 pr-3 text-right font-medium" title="Out-of-sample Sharpe, strategy vs buy and hold">
              OOS Sharpe (vs hold)
            </th>
            <th className="py-2 pr-3 text-right font-medium">t-stat</th>
            <th
              className="py-2 pr-3 text-right font-medium"
              title="Deflated Sharpe ratio: probability the OOS Sharpe beats the best of all configurations tested across experiments by chance"
            >
              Deflated SR
            </th>
            <th className="py-2 pr-3 text-right font-medium" title="One-sided p-value, Benjamini-Hochberg adjusted across experiments">
              p (FDR)
            </th>
            <th className={th}>Verdict</th>
            <th className="py-2 font-medium">Last run</th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {data.map((e: Experiment) => {
            const run = e.latest_run;
            const s = metricCell(run?.metrics, "out_of_sample", "sharpe");
            const t = metricCell(run?.metrics, "out_of_sample", "sharpe_t").value;
            const d = run ? deflated.get(e.id) : undefined;
            const v = oosVerdict(run, d?.run_id === run?.id ? d?.deflated_sharpe : null);
            return (
              <tr key={e.id} aria-selected={selected === e.id} data-testid={`experiment-${e.id}`}
                  className="cursor-pointer hover:bg-muted/50 aria-selected:bg-muted" onClick={() => onSelect(e.id)}>
                <td className="py-2 pr-3">
                  <button type="button" className="text-left font-medium hover:underline" onClick={() => onSelect(e.id)}>
                    {e.name}
                  </button>
                  <p className="font-mono text-[11px] text-muted-foreground">{e.config.feature}</p>
                </td>
                <td className="py-2 pr-3 font-mono text-xs">{e.config.asset.replace("fred:", "")}</td>
                <td className="py-2 pr-3 text-right tabular-nums">
                  {formatNumber(s.value)} <span className="text-muted-foreground">({formatNumber(s.benchmark)})</span>
                </td>
                <td className="py-2 pr-3 text-right tabular-nums">{formatNumber(t)}</td>
                <td className="py-2 pr-3 text-right tabular-nums" data-testid="deflated-sharpe">
                  {formatNumber(d?.deflated_sharpe)}
                </td>
                <td className="py-2 pr-3 text-right tabular-nums">{formatP(d?.p_value_bh)}</td>
                <td className="py-2 pr-3"><Badge tone={v.tone}>{v.label}</Badge></td>
                <td className="py-2 text-xs text-muted-foreground">{run ? formatAge(run.started_at) : "—"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {mt.data && mt.data.n_experiments > 0 ? (
        <p className="mt-2 text-xs text-muted-foreground" data-testid="multiple-testing-note">
          Corrected for {mt.data.n_trials} configurations tested across {mt.data.n_experiments} experiments. A
          deflated Sharpe under 0.95 means the result is consistent with picking the luckiest of them.
        </p>
      ) : null}
    </div>
  ); // prettier-ignore
}

function MetricsTable({ run }: { run: BacktestRun }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm" aria-label="Backtest metrics">
        <thead>
          <tr className={headRow}>
            <th className={th}>Metric</th>
            <th className="py-2 pr-3 text-right font-medium" title="Walk-forward: parameters chosen on past data only">
              Out of sample
            </th>
            <th className="py-2 pr-3 text-right font-medium">Buy and hold (same periods)</th>
            <th className="py-2 text-right font-medium" title="Parameters chosen and evaluated on all data: optimistic">
              In sample (optimistic)
            </th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {METRICS.map((m) => {
            const oos = metricCell(run.metrics, "out_of_sample", m.key);
            const ins = metricCell(run.metrics, "in_sample", m.key);
            return (
              <tr key={m.key}>
                <td className="py-1.5 pr-3" title={m.hint}>{m.label}</td>
                <td className="py-1.5 pr-3 text-right font-medium tabular-nums">{m.format(oos.value)}</td>
                <td className="py-1.5 pr-3 text-right tabular-nums text-muted-foreground">{m.format(oos.benchmark)}</td>
                <td className="py-1.5 text-right tabular-nums text-muted-foreground">{m.format(ins.value)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  ); // prettier-ignore
}

export function ExperimentDetailView({ id }: { id: string }) {
  const { data, error, isPending, refetch } = useExperiment(id);
  const rerun = useRerunExperiment(id);
  if (isPending) return <LoadingRows rows={6} />;
  if (error) return <ErrorState error={error} onRetry={() => void refetch()} />;
  const run = data.runs[0];
  const points = (run?.series.points ?? []).map((p) => ({ ...p, t: Date.parse(p.t) }));
  const bt = run?.config.resolved_backtest ?? {};
  return (
    <div className="flex flex-col gap-4" data-testid="experiment-detail">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="max-w-3xl">
          <h3 className="flex items-center gap-2 text-sm font-semibold">
            <FlaskConical className="size-4 text-muted-foreground" aria-hidden />
            {data.name}
          </h3>
          <p className="mt-1 text-sm">
            <span className="text-muted-foreground">Hypothesis: </span>
            {data.hypothesis}
          </p>
          <p className="mt-1 font-mono text-[11px] text-muted-foreground">
            {data.config.feature} → {data.config.asset}
          </p>
        </div>
        <Button size="sm" variant="outline" onClick={() => rerun.mutate()} disabled={rerun.isPending}>
          {rerun.isPending ? <Loader2 className="size-3.5 animate-spin" aria-hidden /> : <RotateCw className="size-3.5" aria-hidden />}
          Re-run
        </Button>
      </div>
      {!run ? (
        <EmptyState title="Not run yet" />
      ) : run.status === "failed" ? (
        <ErrorState error={new Error(run.error ?? "Backtest failed")} />
      ) : (
        <>
          <p className="text-[11px] text-muted-foreground">
            Walk-forward: parameters (z-score lookback, entry threshold, direction) are chosen on each expanding training
            window by net Sharpe, then applied unchanged to the next {String(bt.test_periods ?? "")} periods.{" "}
            {run.series.n_configs_tested} configurations searched per fold. Costs {String(bt.cost_bps ?? "")} bps +
            slippage {String(bt.slippage_bps ?? "")} bps per unit of position change; sizing: {String(bt.sizing ?? "")}
            {bt.long_only ? ", long only" : ""}.
          </p>
          <div className="grid gap-4 xl:grid-cols-5">
            <div className="xl:col-span-3">
              <p className="text-xs font-medium">Out-of-sample growth of 1</p>
              <EquityChart points={points} />
              <p className="mt-3 text-xs font-medium">Drawdown</p>
              <DrawdownChart points={points} />
            </div>
            <div className="xl:col-span-2">
              <MetricsTable run={run} />
            </div>
          </div>
          <details className="text-xs">
            <summary className="cursor-pointer text-muted-foreground">Walk-forward folds ({run.folds.length})</summary>
            <table className="mt-2 w-full" aria-label="Walk-forward folds">
              <thead>
                <tr className={headRow}>
                  <th className={th}>Test window</th>
                  <th className={th}>Trained on</th>
                  <th className="py-2 pr-3 text-right font-medium">Lookback</th>
                  <th className="py-2 pr-3 text-right font-medium">Threshold</th>
                  <th className="py-2 pr-3 text-right font-medium">Direction</th>
                  <th className="py-2 text-right font-medium">Train Sharpe</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {run.folds.map((f) => (
                  <tr key={f.test_start}>
                    <td className="py-1 pr-3">{formatDay(f.test_start)} → {formatDay(f.test_end)}</td>
                    <td className="py-1 pr-3 text-muted-foreground">{formatDay(f.train_start)} → {formatDay(f.train_end)}</td>
                    <td className="py-1 pr-3 text-right tabular-nums">{f.lookback}</td>
                    <td className="py-1 pr-3 text-right tabular-nums">{f.threshold}</td>
                    <td className="py-1 pr-3 text-right">{f.direction > 0 ? "with signal" : "against signal"}</td>
                    <td className="py-1 text-right tabular-nums">{formatNumber(f.train_sharpe)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
          <p className="text-[11px] text-muted-foreground">
            Run {formatAge(run.started_at)} · code <span className="font-mono">{run.code_version?.slice(0, 10) ?? "unknown"}</span> ·
            data <span className="font-mono">{run.data_snapshot_hash?.slice(0, 12)}</span> (identical inputs reproduce identical results)
          </p>
        </>
      )}
      <p className="rounded-md border border-warning/30 bg-warning/5 p-2 text-[11px]">{data.disclaimer}</p>
    </div>
  ); // prettier-ignore
}

export function NewExperimentForm({ sel, onCreated }: { sel: Selection; onCreated: (id: string) => void }) {
  const create = useCreateExperiment();
  const [name, setName] = useState("");
  const [hypothesis, setHypothesis] = useState("");
  const [sizing, setSizing] = useState("sign");
  const [longOnly, setLongOnly] = useState(false);
  const [cost, setCost] = useState(5);
  const [slippage, setSlippage] = useState(5);
  const feature = featureKey(sel.base, sel.frequency, sel.transform);

  async function submit(e: FormEvent) {
    e.preventDefault();
    const detail = await create.mutateAsync({
      name,
      hypothesis,
      config: {
        feature,
        asset: sel.asset,
        backtest: { sizing, long_only: longOnly, cost_bps: cost, slippage_bps: slippage },
      },
    });
    setName("");
    setHypothesis("");
    onCreated(detail.id);
  }

  const input = "h-8 rounded-md border bg-background px-2 text-sm";
  return (
    <form onSubmit={(e) => void submit(e)} className="flex flex-col gap-3" aria-label="New experiment">
      <p className="text-[11px] text-muted-foreground">
        Uses the explorer&apos;s current selection: <span className="font-mono">{feature}</span> →{" "}
        <span className="font-mono">{sel.asset}</span>. Write the hypothesis before you look at results.
      </p>
      <div className="grid gap-3 md:grid-cols-2">
        <label className="flex flex-col gap-1 text-xs text-muted-foreground">
          Name
          <input className={input} value={name} onChange={(e) => setName(e.target.value)} required minLength={3} maxLength={200} />
        </label>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            Sizing
            <select className={input} value={sizing} onChange={(e) => setSizing(e.target.value)}>
              <option value="sign">Sign (±1)</option>
              <option value="linear">Scaled by z</option>
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            Cost (bps)
            <input type="number" min={0} max={200} className={input} value={cost} onChange={(e) => setCost(Number(e.target.value))} />
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            Slippage (bps)
            <input type="number" min={0} max={200} className={input} value={slippage} onChange={(e) => setSlippage(Number(e.target.value))} />
          </label>
          <label className="flex items-end gap-2 pb-1.5 text-xs text-muted-foreground">
            <input type="checkbox" checked={longOnly} onChange={(e) => setLongOnly(e.target.checked)} /> Long only
          </label>
        </div>
      </div>
      <label className="flex flex-col gap-1 text-xs text-muted-foreground">
        Hypothesis (the economic reason the feature should lead returns)
        <textarea className="min-h-16 rounded-md border bg-background p-2 text-sm" value={hypothesis}
                  onChange={(e) => setHypothesis(e.target.value)} required minLength={10} maxLength={2000} />
      </label>
      {create.error ? <ErrorState error={create.error} /> : null}
      <div>
        <Button type="submit" size="sm" disabled={create.isPending}>
          {create.isPending ? <Loader2 className="size-3.5 animate-spin" aria-hidden /> : null}
          Create and run backtest
        </Button>
      </div>
    </form>
  ); // prettier-ignore
}
