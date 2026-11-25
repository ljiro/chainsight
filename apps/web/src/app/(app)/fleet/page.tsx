import type { Metadata } from "next";
import { PageHeader } from "@/components/page-header";
import { PlannedFeature } from "@/components/planned-feature";

export const metadata: Metadata = { title: "Fleet map" };

export default function FleetPage() {
  return (
    <>
      <PageHeader
        title="Global fleet map"
        description="Live AIS vessel positions. AIS shows where a vessel is; it is never treated as the location of an individual container."
      />
      <PlannedFeature
        title="Live vessel map"
        phase={2}
        description="MapLibre map fed by the AISStream consumer through a WebSocket, with replay mode when no API key is configured."
        items={[
          "Live vessel positions",
          "Vessel type and carrier filters",
          "Vessel details panel",
          "Historical tracks",
          "Port markers",
          "Per-position data freshness",
        ]}
      />
    </>
  );
}
