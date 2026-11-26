import type { Metadata } from "next";
import { PageHeader } from "@/components/page-header";
import { ResearchView } from "@/components/research/research-view";

export const metadata: Metadata = { title: "Research" };

export default function ResearchPage() {
  return (
    <>
      <PageHeader
        title="Quantitative maritime research"
        description="Point-in-time maritime indicators, statistical tests and walk-forward backtests. Research findings, not trading recommendations."
      />
      <ResearchView />
    </>
  );
}
