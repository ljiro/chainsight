import type { Metadata } from "next";
import { Suspense } from "react";
import { FleetView } from "@/components/fleet/fleet-view";
import { PageHeader } from "@/components/page-header";

export const metadata: Metadata = { title: "Fleet map" };

export default function FleetPage() {
  return (
    <>
      <PageHeader
        title="Global fleet map"
        description="Latest AIS vessel positions with live updates. AIS shows where a vessel is; it is never treated as the location of an individual container."
      />
      <Suspense>
        <FleetView />
      </Suspense>
    </>
  );
}
