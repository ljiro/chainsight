import type { Metadata } from "next";
import { PageHeader } from "@/components/page-header";
import { PlannedFeature } from "@/components/planned-feature";

export const metadata: Metadata = { title: "Shipments" };

export default function ShipmentsPage() {
  return (
    <>
      <PageHeader
        title="Shipment tracking"
        description="Track containers, bookings, and bills of lading through carrier APIs or CSV import."
      />
      <PlannedFeature
        title="Shipment search & timeline"
        phase={3}
        description="DCSA-based milestones, with planned, estimated, and actual events kept side by side."
        items={[
          "Search by container, booking, or B/L",
          "Shipment list with status",
          "Milestone timeline",
          "Assigned vessel (as reported by the carrier)",
          "ETA prediction",
          "Delay risk",
        ]}
      />
    </>
  );
}
