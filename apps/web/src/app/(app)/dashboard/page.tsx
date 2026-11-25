import { AlertTriangle, Package, ShieldAlert, Ship } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";
import { CarrierStatusList } from "@/components/dashboard/carrier-status-list";
import { DataSourcesCard } from "@/components/dashboard/data-sources-card";
import { KpiCard } from "@/components/dashboard/kpi-card";
import { SystemHealthCard } from "@/components/dashboard/system-health-card";
import { PageHeader } from "@/components/page-header";
import { PlannedFeature } from "@/components/planned-feature";
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
        <KpiCard label="Active shipments" value={null} icon={Package} unavailableReason="Shipment tracking arrives in Phase 3" />
        <KpiCard label="Monitored vessels" value={null} icon={Ship} unavailableReason="AIS ingestion arrives in Phase 2" />
        <KpiCard label="Delayed shipments" value={null} icon={AlertTriangle} unavailableReason="Requires shipment events (Phase 3)" />
        <KpiCard label="High-risk shipments" value={null} icon={ShieldAlert} unavailableReason="Requires delay model (Phase 4)" />
      </section>

      <div className="grid gap-6 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <DataSourcesCard />
        </div>
        <SystemHealthCard />
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-1">
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
        <div className="flex flex-col gap-6 lg:col-span-2">
          <PlannedFeature
            title="Fleet activity & port congestion"
            phase={2}
            description="Vessel counts by status, arrivals approaching ports, and congestion indicators from AIS and IMF PortWatch."
          />
          <PlannedFeature
            title="Recent alerts"
            phase={2}
            description="Data-freshness, ingestion, and vessel-anomaly alerts raised by the platform."
          />
        </div>
      </div>
    </>
  );
}
