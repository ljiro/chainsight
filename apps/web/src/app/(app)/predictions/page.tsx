import type { Metadata } from "next";
import { PageHeader } from "@/components/page-header";
import { PredictionsView } from "@/components/predictions/predictions-view";

export const metadata: Metadata = { title: "Predictions" };

export default function PredictionsPage() {
  return (
    <>
      <PageHeader
        title="Predictive intelligence"
        description="Every prediction is versioned, timestamped, and kept, so it can be evaluated once the real outcome is known."
      />
      <PredictionsView />
    </>
  );
}
