import type { Metadata } from "next";
import { PageHeader } from "@/components/page-header";
import { ShipmentsView } from "@/components/shipments/shipments-view";

export const metadata: Metadata = { title: "Shipments" };

export default function ShipmentsPage() {
  return (
    <>
      <PageHeader
        title="Shipment tracking"
        description="Track containers, bookings and bills of lading through carrier integrations or CSV import. Planned, estimated and actual milestones are kept side by side."
      />
      <ShipmentsView />
    </>
  );
}
