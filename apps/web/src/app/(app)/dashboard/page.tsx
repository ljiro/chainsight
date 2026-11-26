import type { Metadata } from "next";
import Link from "next/link";
import { CarrierStatusList } from "@/components/dashboard/carrier-status-list";
import { DataSourcesCard } from "@/components/dashboard/data-sources-card";
import {
  AlertsCard,
  ApproachingCard,
  FleetActivityCard,
  MonitoredVesselsKpi,
  ShipmentKpis,
} from "@/components/dashboard/fleet-cards";
import { HighRiskKpi } from "@/components/predictions/prediction-cards";
import { SystemHealthCard } from "@/components/dashboard/system-health-card";
import { PageHeader } from "@/components/page-header";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

export const metadata: Metadata = { title: "Dashboard" };

export default function DashboardPage() {
  return (
    <>
      <PageHeader
        title="Operations overview"
        description="Shipments, fleet activity, and risk at a glance. Metrics stay empty until a real data source can produce them."
      />

      <section aria-label="Key metrics" className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <ShipmentKpis />
        <MonitoredVesselsKpi />
        <HighRiskKpi />
      </section>

      <div className="grid gap-6 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <FleetActivityCard />
        </div>
        <AlertsCard />
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <DataSourcesCard />
        </div>
        <SystemHealthCard />
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        <ApproachingCard />
        <Card className="lg:col-span-2">
          <CardHeader>
            <div className="flex items-center justify-between">
              <CardTitle>Carrier integrations</CardTitle>
              <Link href="/carriers" className="text-xs font-medium text-primary hover:underline">
                View all
              </Link>
            </div>
            <CardDescription>Container-level tracking access per carrier.</CardDescription>
          </CardHeader>
          <CardContent>
            <CarrierStatusList compact />
          </CardContent>
        </Card>
      </div>
    </>
  );
}
