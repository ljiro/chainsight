"use client";

import { StatusBadge } from "@/components/status-badge";
import { EmptyState, ErrorState, LoadingRows } from "@/components/query-state";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useDataSources } from "@/lib/queries";
import { formatAge, humanize } from "@/lib/utils";

export function DataSourcesCard() {
  const { data, error, isPending, refetch } = useDataSources();

  return (
    <Card>
      <CardHeader>
        <CardTitle>External data sources</CardTitle>
        <CardDescription>
          Connection status, data mode, and freshness of every provider. Nothing is shown as live unless it is.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {isPending ? (
          <LoadingRows rows={4} />
        ) : error ? (
          <ErrorState error={error} onRetry={() => void refetch()} />
        ) : data.items.length === 0 ? (
          <EmptyState title="No data sources registered" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-[11px] tracking-wide text-muted-foreground uppercase">
                  <th className="py-2 pr-3 font-medium">Source</th>
                  <th className="py-2 pr-3 font-medium">Status</th>
                  <th className="py-2 pr-3 font-medium">Mode</th>
                  <th className="py-2 pr-3 font-medium">Freshness</th>
                  <th className="py-2 font-medium">Last update</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {data.items.map((s) => (
                  <tr key={s.key} data-testid={`source-${s.key}`}>
                    <td className="py-2.5 pr-3">
                      {s.homepage_url ? (
                        <a href={s.homepage_url} target="_blank" rel="noreferrer" className="font-medium hover:underline">
                          {s.name}
                        </a>
                      ) : (
                        <span className="font-medium">{s.name}</span>
                      )}
                      <p className="text-xs text-muted-foreground">{s.category === "ais" ? "AIS" : humanize(s.category)}</p>
                    </td>
                    <td className="py-2.5 pr-3">
                      <StatusBadge kind="source" value={s.status} />
                    </td>
                    <td className="py-2.5 pr-3">
                      <StatusBadge kind="mode" value={s.data_mode} />
                    </td>
                    <td className="py-2.5 pr-3">
                      <StatusBadge kind="freshness" value={s.freshness} />
                    </td>
                    <td className="py-2.5 text-xs whitespace-nowrap text-muted-foreground">
                      {formatAge(s.last_success_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
