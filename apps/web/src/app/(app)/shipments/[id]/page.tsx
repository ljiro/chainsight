import type { Metadata } from "next";
import { ShipmentView } from "@/components/shipments/shipment-view";

export const metadata: Metadata = { title: "Shipment" };

export default async function ShipmentPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <ShipmentView id={id} />;
}
