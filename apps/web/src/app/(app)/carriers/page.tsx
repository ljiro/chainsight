import type { Metadata } from "next";
import { CarrierStatusList } from "@/components/dashboard/carrier-status-list";
import { PageHeader } from "@/components/page-header";
import { PlannedFeature } from "@/components/planned-feature";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

export const metadata: Metadata = { title: "Carriers" };

export default function CarriersPage() {
  return (
    <>
      <PageHeader
        title="Carrier intelligence"
        description="Supported carriers and the state of ChainSight's integration with each. Fleet visibility (AIS) and container-level tracking (carrier APIs) are separate capabilities."
      />
      <Card>
        <CardHeader>
          <CardTitle>Supported carriers</CardTitle>
          <CardDescription>
            Integration status for container tracking. &ldquo;Unavailable&rdquo; means no adapter exists yet; it is not a
            statement about the carrier&apos;s own API.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <CarrierStatusList />
        </CardContent>
      </Card>
      <PlannedFeature
        title="Fleet & performance comparison"
        phase={3}
        description="Comparisons across carriers once vessel and shipment data exist."
        items={[
          "Fleet size represented in available AIS data",
          "Vessel activity by carrier",
          "Schedule reliability and delay rates",
          "Side-by-side carrier comparison",
        ]}
      />
    </>
  );
}
