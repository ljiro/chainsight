import type { Metadata } from "next";
import { PageHeader } from "@/components/page-header";
import { PlannedFeature } from "@/components/planned-feature";

export const metadata: Metadata = { title: "Predictions" };

export default function PredictionsPage() {
  return (
    <>
      <PageHeader
        title="Predictive intelligence"
        description="Every prediction is versioned, timestamped, and kept, so it can be evaluated once the real outcome is known."
      />
      <PlannedFeature
        title="Models & forecasts"
        phase={4}
        description="Baselines first, then gradient-boosted models with time-based validation. Models trained on synthetic data are labeled as such."
        items={["Vessel ETA forecasts", "Shipment delay probabilities", "Port congestion forecasts", "Model performance & calibration"]}
      />
    </>
  );
}
