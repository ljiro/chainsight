import type { Metadata } from "next";
import { CarrierComparison } from "@/components/carriers/carrier-comparison";
import { PageHeader } from "@/components/page-header";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

export const metadata: Metadata = { title: "Carriers" };

export default function CarriersPage() {
  return (
    <>
      <PageHeader
        title="Carrier intelligence"
        description="Integration status, fleet seen in AIS, and shipment reliability per carrier. Fleet visibility (AIS) and container-level tracking (carrier APIs) are separate capabilities."
      />
      <Card>
        <CardHeader>
          <CardTitle>Carrier comparison</CardTitle>
          <CardDescription>
            &ldquo;Authentication required&rdquo; means a generic DCSA Track &amp; Trace adapter exists but no
            credentials are configured. It does not mean the carrier&apos;s API was verified.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <CarrierComparison />
        </CardContent>
      </Card>
    </>
  );
}
