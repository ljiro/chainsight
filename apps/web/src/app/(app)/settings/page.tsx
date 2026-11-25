import type { Metadata } from "next";
import { DataSourcesCard } from "@/components/dashboard/data-sources-card";
import { SystemHealthCard } from "@/components/dashboard/system-health-card";
import { PageHeader } from "@/components/page-header";
import { PlannedFeature } from "@/components/planned-feature";

export const metadata: Metadata = { title: "Settings" };

export default function SettingsPage() {
  return (
    <>
      <PageHeader
        title="Settings"
        description="Provider credentials are configured on the server through environment variables. They are never entered in or sent to the browser."
      />
      <div className="grid gap-6 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <DataSourcesCard />
        </div>
        <SystemHealthCard />
      </div>
      <PlannedFeature
        title="Organization, users & API keys"
        phase={6}
        description="Arrives with authentication and role-based access control."
        items={["Organization settings", "User management and roles", "API key issuance and revocation", "Integration configuration"]}
      />
    </>
  );
}
