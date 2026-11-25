import type { Metadata } from "next";
import { PageHeader } from "@/components/page-header";
import { PlannedFeature } from "@/components/planned-feature";

export const metadata: Metadata = { title: "Vessel" };

export default async function VesselPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <>
      <PageHeader
        title="Vessel details"
        description={
          <>
            Requested vessel <span className="font-mono">{id}</span>. The vessel registry is not built yet.
          </>
        }
      />
      <PlannedFeature
        title="Vessel profile"
        phase={2}
        description="Identity resolved from AIS static data (MMSI/IMO), with sources and history."
        items={[
          "Vessel identity",
          "Carrier/operator association (with source)",
          "Current location",
          "Historical movement",
          "Voyage information",
          "Port call history",
          "Anomaly alerts",
        ]}
      />
    </>
  );
}
