import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { congestionRows } from "@/components/predictions/congestion-chart";
import { HighRiskKpi, ShipmentDelayCard, VesselEtaCard } from "@/components/predictions/prediction-cards";
import { ModelsTable } from "@/components/predictions/predictions-view";
import { fallbackLabel, formatProbability, formatRange, headlineMetrics, intervalLabel, riskLevel } from "@/lib/predictions";
import type { DelayPrediction, EtaPrediction, ModelVersion } from "@/lib/types";
import { mockFetch, renderWithQuery } from "@/test/utils";

const model = (over: Partial<ModelVersion>): ModelVersion => ({
  id: "m1", model_name: "eta", version: "eta-qgbm-1", algorithm: "quantile HGB", status: "production",
  is_synthetic_trained: false, n_train: 500, n_test: 150, training_window_start: "2026-11-25T00:00:00Z",
  training_window_end: "2026-11-26T00:00:00Z", feature_schema: {}, notes: null, created_at: "2026-11-26T00:00:00Z",
  evaluations: [{ split: "test", metrics: { mae_hours: 0.948, interval_coverage: 0.921 }, n_samples: 150, data_mode: "real", evaluated_at: "2026-11-26T00:00:00Z" }],
  predictions: 12, ...over,
}); // prettier-ignore

const eta = (over: Partial<EtaPrediction>): EtaPrediction => ({
  id: "p1", vessel_id: "v1", vessel_name: "MV TEST", mmsi: 1, target_unlocode: "NLRTM", target_port_name: "Rotterdam",
  predicted_at: "2026-11-26T10:00:00Z", input_data_as_of: "2026-11-26T09:58:00Z",
  predicted_arrival_at: "2026-11-26T20:00:00Z", lower_bound_at: "2026-11-26T18:00:00Z", upper_bound_at: "2026-11-26T22:00:00Z",
  interval_level: null, remaining_distance_nm: 140, interval_method: "heuristic_pm20pct", fallback_reason: null,
  actual_arrival_at: null, error_hours: null, model_version: "baseline-distance-over-speed-v1",
  model_algorithm: "rule", is_synthetic_trained: false, ...over,
}); // prettier-ignore

const delay = (over: Partial<DelayPrediction>): DelayPrediction => ({
  id: "d1", shipment_id: "s1", reference_value: "DEMO123", carrier_name: "Demo Carrier", destination_unlocode: "NLRTM",
  predicted_at: "2026-11-26T10:00:00Z", input_data_as_of: "2026-11-26T09:00:00Z", delay_probability: 0.82,
  threshold_hours: 12, calibration_method: "isotonic", actual_delayed: null, actual_delay_hours: null,
  shipment_data_mode: "synthetic", factors: [{ factor: "replanned_later", text: "Arrival re-planned 30 h later than first plan", value_hours: 30 }],
  model_version: "delay-hgb-1", model_algorithm: "HGB", is_synthetic_trained: true, ...over,
}); // prettier-ignore

describe("prediction helpers", () => {
  it("uses the backend's risk cut-offs", () => {
    expect(riskLevel(0.7).level).toBe("high");
    expect(riskLevel(0.4).level).toBe("medium");
    expect(riskLevel(0.39).level).toBe("low");
    expect(formatProbability(0.004)).toBe("<1%");
    expect(formatProbability(0.823)).toBe("82%");
  });

  it("never presents the heuristic band as a calibrated interval", () => {
    expect(intervalLabel({ interval_method: "heuristic_pm20pct", interval_level: null })).toMatch(/not a calibrated/);
    expect(intervalLabel({ interval_method: "quantile_gbm_p10_p90", interval_level: 0.8 })).toMatch(/^80% interval/);
    expect(fallbackLabel("out_of_training_domain")).toMatch(/baseline/);
    expect(fallbackLabel(null)).toBeNull();
    expect(formatRange("2026-11-26T13:10:00Z", "2026-11-26T15:40:00Z")).toBe("13:10–15:40 UTC");
    expect(formatRange("2026-11-26T23:10:00Z", "2026-11-27T01:40:00Z")).toBe("26 Nov, 23:10 UTC – 27 Nov, 01:40 UTC");
  });

  it("picks headline metrics from the held-out test split", () => {
    const h = headlineMetrics(model({}))!;
    expect(h.split).toBe("test");
    expect(h.metrics.map((m) => `${m.label}=${m.value}`)).toEqual(["MAE=0.95 h", "Interval coverage=92%"]);
    expect(headlineMetrics(model({ evaluations: [] }))).toBeNull();
  });

  it("joins observations and forecast on one axis, handing off at the last observation", () => {
    const rows = congestionRows(
      [{ t: "2026-11-26T08:00:00Z", value: 4 }, { t: "2026-11-26T09:00:00Z", value: 5 }],
      [{ target_time: "2026-11-26T10:00:00Z", value: 6, lower: null, upper: null }],
    );
    expect(rows.map((r) => [r.observed, r.forecast])).toEqual([[4, undefined], [5, 5], [undefined, 6]]);
    expect(rows.every((r) => r.band === undefined)).toBe(true); // no invented intervals
  });
});

describe("prediction components", () => {
  it("labels synthetic-trained models and the ETA fallback baseline in the registry", async () => {
    mockFetch(() => ({
      body: [
        model({}),
        model({ id: "m2", version: "baseline-distance-over-speed-v1", status: "archived", feature_schema: { type: "rule" }, evaluations: [] }),
        model({ id: "m3", model_name: "delay", version: "delay-hgb-1", is_synthetic_trained: true, evaluations: [] }),
        model({ id: "m4", version: "eta-old", status: "archived", predictions: 0 }),
      ],
    })); // prettier-ignore
    renderWithQuery(<ModelsTable />);
    const delayRow = await screen.findByTestId("model-delay-hgb-1");
    expect(within(delayRow).getByText("Synthetic-trained")).toBeInTheDocument();
    expect(within(screen.getByTestId("model-baseline-distance-over-speed-v1")).getByText("Fallback")).toBeInTheDocument();
    expect(within(screen.getByTestId("model-eta-qgbm-1")).queryByText("Synthetic-trained")).toBeNull();
    expect(screen.queryByTestId("model-eta-old")).toBeNull(); // superseded and never used
  });

  it("shows the ETA with its model version, input time and an honest range label", async () => {
    mockFetch(() => ({ body: [eta({ fallback_reason: "out_of_training_domain" })] }));
    renderWithQuery(<VesselEtaCard vesselId="v1" />);
    const card = await screen.findByTestId("vessel-eta");
    expect(within(card).getByText(/not a calibrated interval/)).toBeInTheDocument();
    expect(within(card).getByText(/Outside the trained model's range/)).toBeInTheDocument();
    expect(within(card).getByText("baseline-distance-over-speed-v1")).toBeInTheDocument();
    expect(within(card).getByText("Input data as of")).toBeInTheDocument();
  });

  it("shows delay risk with factors and a synthetic label", async () => {
    mockFetch(() => ({ body: [delay({})] }));
    renderWithQuery(<ShipmentDelayCard shipmentId="s1" />);
    const card = await screen.findByTestId("shipment-delay");
    expect(within(card).getByText("82%")).toBeInTheDocument();
    expect(within(card).getByText("High risk")).toBeInTheDocument();
    expect(within(card).getByText(/re-planned 30 h later/)).toBeInTheDocument();
    expect(within(card).getByText("Synthetic-trained")).toBeInTheDocument();
  });

  it("explains a missing delay prediction instead of showing a number", async () => {
    mockFetch(() => ({ body: [] }));
    renderWithQuery(<ShipmentDelayCard shipmentId="s1" />);
    expect(await screen.findByText("No delay prediction")).toBeInTheDocument();
  });

  it("leaves the high-risk KPI empty when no delay model is in production", async () => {
    mockFetch(() => ({
      body: { eta_vessels: 3, high_risk: 0, delay_model_synthetic: null, open_anomalies: 0, congestion_ports: 0, eta_resolved: 0 },
    }));
    renderWithQuery(<HighRiskKpi />);
    expect(await screen.findByText("No delay model in production")).toBeInTheDocument();
    expect(screen.getByLabelText("High-risk shipments: no data")).toBeInTheDocument();
  });

  it("labels the high-risk count when it comes from a synthetic-trained model", async () => {
    mockFetch(() => ({
      body: { eta_vessels: 3, high_risk: 2, delay_model_synthetic: true, open_anomalies: 0, congestion_ports: 0, eta_resolved: 0 },
    }));
    renderWithQuery(<HighRiskKpi />);
    expect(await screen.findByText("2")).toBeInTheDocument();
    expect(screen.getByText("Synthetic-trained")).toBeInTheDocument();
  });
});
