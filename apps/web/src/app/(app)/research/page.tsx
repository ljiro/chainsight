import type { Metadata } from "next";
import { PageHeader } from "@/components/page-header";
import { PlannedFeature } from "@/components/planned-feature";

export const metadata: Metadata = { title: "Research" };

export default function ResearchPage() {
  return (
    <>
      <PageHeader
        title="Quantitative maritime research"
        description="A research environment, not a trading system. Results are research findings, not trading recommendations."
      />
      <PlannedFeature
        title="Research workspace"
        phase={5}
        description="Point-in-time maritime features (aligned on availability time, not just event time) with walk-forward evaluation."
        items={[
          "Maritime indicators & time series",
          "Feature exploration",
          "Correlation & lead-lag analysis",
          "Research experiments",
          "Backtests with costs & slippage",
          "Out-of-sample evaluation",
        ]}
      />
    </>
  );
}
