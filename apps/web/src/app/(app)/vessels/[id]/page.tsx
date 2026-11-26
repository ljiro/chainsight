import type { Metadata } from "next";
import { VesselView } from "@/components/vessel/vessel-view";

export const metadata: Metadata = { title: "Vessel" };

export default async function VesselPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <VesselView id={id} />;
}
