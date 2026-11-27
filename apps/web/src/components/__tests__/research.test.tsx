import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ExperimentDetailView, ExperimentsTable, NewExperimentForm } from "@/components/research/experiments";
import { MethodologyNote } from "@/components/research/research-view";
import { describeRule, featureKey, formatP, oosVerdict, parseFeatureKey } from "@/lib/research";
import type { BacktestRunSummary, Experiment, ExperimentDetail } from "@/lib/types";
import { mockFetch, renderWithQuery } from "@/test/utils";
import { render } from "@testing-library/react";

// Recharts needs real layout; the charts themselves are exercised in the browser.
vi.mock("@/components/research/charts", () => ({
  EquityChart: () => <div data-testid="equity-chart" />,
  DrawdownChart: () => <div data-testid="drawdown-chart" />,
}));

const run = (oos: number, bench: number, t: number, status: BacktestRunSummary["status"] = "succeeded"): BacktestRunSummary => ({
  id: "r1", status, started_at: "2026-11-26T12:00:00Z", finished_at: "2026-11-26T12:00:01Z", error: status === "failed" ? "InsufficientData: no data" : null,
  code_version: "abcdef1234567", data_snapshot_hash: "0123456789abcdef",
  metrics: {
    "out_of_sample.sharpe": { value: oos, benchmark: bench },
    "out_of_sample.sharpe_t": { value: t, benchmark: null },
    "out_of_sample.total_return": { value: -0.05, benchmark: 0.04 },
    "in_sample.sharpe": { value: 0.9, benchmark: 0.3 },
  },
}); // prettier-ignore

const experiment = (id: string, name: string, r: BacktestRunSummary | null): Experiment => ({
  id, name, hypothesis: "Tanker transits lead Brent returns.", status: "completed",
  config: { feature: "composite:hormuz_bab_tanker|W|yoy", asset: "fred:DCOILBRENTEU" },
  created_at: "2026-11-26T12:00:00Z", updated_at: "2026-11-26T12:00:00Z", n_runs: 1, latest_run: r,
}); // prettier-ignore

describe("research helpers", () => {
  it("round-trips feature keys", () => {
    expect(featureKey("composite:x", "W", "yoy")).toBe("composite:x|W|yoy");
    expect(parseFeatureKey("a|M|z26")).toEqual({ base: "a", frequency: "M", transform: "z26" });
    expect(parseFeatureKey("bad")).toBeNull();
  });

  it("describes availability rules in words", () => {
    expect(describeRule({ kind: "weekly_release", weekday: 1, hour: 12, min_lag_days: 4, safety_days: 7 })).toBe(
      "Weekly release (Tue 12:00 UTC), ≥4 days after the day, +7 days safety",
    );
    expect(describeRule({ kind: "next_day", lag_days: 0 })).toMatch(/closing price/);
  });

  it("never prints a p-value of zero", () => {
    expect(formatP(0)).toBe("<0.001");
    expect(formatP(0.04321)).toBe("0.043");
  });

  it("is conservative about out-of-sample verdicts", () => {
    expect(oosVerdict(run(0.13, 0.23, 0.3)).label).toBe("No significant edge");
    expect(oosVerdict(run(-1.09, 0.28, -2.5)).label).toBe("Below buy-and-hold");
    expect(oosVerdict(run(1.5, 0.2, 3.1)).label).toMatch(/verify/);
    expect(oosVerdict(run(0, 0, 0, "failed")).label).toBe("Run failed");
    expect(oosVerdict(run(1.5, 0.2, 3.1), 0.6).label).toBe("No edge after deflation");
    expect(oosVerdict(run(1.5, 0.2, 3.1), 0.99).label).toMatch(/verify/);
  });
});

describe("research components", () => {
  it("states the research disclaimer and the availability method", () => {
    render(<MethodologyNote />);
    expect(screen.getByRole("note")).toHaveTextContent(/not a trading system/);
    expect(screen.getByRole("note")).toHaveTextContent(/7-day safety margin/);
  });

  it("lists experiments with OOS Sharpe next to buy-and-hold and a verdict", async () => {
    mockFetch((path) =>
      path.endsWith("/multiple-testing")
        ? { body: { n_experiments: 0, n_trials: 0, trial_sharpe_variance: null, experiments: [], method: "", disclaimer: "" } }
        : { body: [experiment("e1", "Gulf tanker transits vs Brent", run(0.13, 0.23, 0.3))] },
    );
    const onSelect = vi.fn();
    renderWithQuery(<ExperimentsTable selected={null} onSelect={onSelect} />);
    const row = await screen.findByTestId("experiment-e1");
    expect(within(row).getByText("No significant edge")).toBeInTheDocument();
    expect(row).toHaveTextContent("0.13 (0.23)");
    fireEvent.click(within(row).getByRole("button", { name: "Gulf tanker transits vs Brent" }));
    expect(onSelect).toHaveBeenCalledWith("e1");
  });

  it("deflates a strong-looking Sharpe for the number of configurations tested", async () => {
    mockFetch((path) =>
      path.endsWith("/multiple-testing")
        ? { body: { n_experiments: 3, n_trials: 54, trial_sharpe_variance: 0.002, method: "", disclaimer: "",
                    experiments: [{ experiment_id: "e1", name: "x", run_id: "r1", n_periods: 150, sharpe_per_period: 0.2,
                                    skew: 0, kurtosis: 3, moments_assumed_normal: false, sr_benchmark: 0.1,
                                    deflated_sharpe: 0.62, psr_zero: 0.99, p_value: 0.01, p_value_bh: 0.03 }] } }
        : { body: [experiment("e1", "Lucky", run(1.5, 0.2, 3.1))] },
    ); // prettier-ignore
    renderWithQuery(<ExperimentsTable selected={null} onSelect={vi.fn()} />);
    const row = await screen.findByTestId("experiment-e1");
    await waitFor(() => expect(within(row).getByTestId("deflated-sharpe")).toHaveTextContent("0.62"));
    expect(within(row).getByText("No edge after deflation")).toBeInTheDocument();
    expect(screen.getByTestId("multiple-testing-note")).toHaveTextContent("54 configurations");
  });

  it("shows out-of-sample, benchmark and optimistic in-sample columns side by side", async () => {
    const detail: ExperimentDetail = {
      ...experiment("e1", "Gulf", null),
      disclaimer: "Research output, not investment advice.",
      runs: [{ ...run(0.13, 0.23, 0.3), config: { feature: "f", asset: "a", resolved_backtest: { test_periods: 26, cost_bps: 5, slippage_bps: 5, sizing: "sign" } },
               series: { points: [], n_configs_tested: 18 }, folds: [] }],
    }; // prettier-ignore
    mockFetch(() => ({ body: detail }));
    renderWithQuery(<ExperimentDetailView id="e1" />);
    const view = await screen.findByTestId("experiment-detail");
    expect(within(view).getByText(/Hypothesis:/)).toBeInTheDocument();
    expect(within(view).getByRole("columnheader", { name: /In sample \(optimistic\)/ })).toBeInTheDocument();
    expect(within(view).getByText(/18 configurations searched per fold/)).toBeInTheDocument();
    expect(within(view).getByText("-5.0%")).toBeInTheDocument();
    expect(within(view).getByText(/not investment advice/)).toBeInTheDocument();
  });

  it("surfaces a failed run instead of empty metrics", async () => {
    mockFetch(() => ({ body: { ...experiment("e1", "Gulf", null), disclaimer: "d", runs: [{ ...run(0, 0, 0, "failed"), config: { feature: "f", asset: "a" }, series: {}, folds: [] }] } }));
    renderWithQuery(<ExperimentDetailView id="e1" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("InsufficientData: no data");
  }); // prettier-ignore

  it("creates an experiment from the explorer selection with a written hypothesis", async () => {
    const fetchMock = mockFetch(() => ({ status: 201, body: { ...experiment("e9", "New", null), runs: [], disclaimer: "d" } }));
    const onCreated = vi.fn();
    renderWithQuery(
      <NewExperimentForm sel={{ base: "composite:suez_share_tanker", frequency: "W", transform: "z26", asset: "fred:DCOILBRENTEU" }} onCreated={onCreated} />,
    ); // prettier-ignore
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Red Sea" } });
    fireEvent.change(screen.getByLabelText(/Hypothesis/), { target: { value: "Rerouting tightens supply." } });
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: /Create and run/ }));
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith("e9"));
    // The stub types only the URL argument; fetch receives (url, init) at runtime.
    const [, init] = fetchMock.mock.calls[0] as unknown as [RequestInfo, RequestInit];
    const body = JSON.parse(init.body as string);
    expect(body.config).toEqual({
      feature: "composite:suez_share_tanker|W|z26",
      asset: "fred:DCOILBRENTEU",
      backtest: { sizing: "sign", long_only: true, cost_bps: 5, slippage_bps: 5 },
    });
  });
});
