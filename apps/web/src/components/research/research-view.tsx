"use client";

import { useState } from "react";
import { ExperimentDetailView, ExperimentsTable, NewExperimentForm } from "@/components/research/experiments";
import { Explorer, useSelection } from "@/components/research/explorer";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useExperiments } from "@/lib/queries";

export function MethodologyNote() {
  return (
    <div className="rounded-md border border-warning/30 bg-warning/5 p-3 text-xs" role="note">
      <p className="font-medium">A research environment, not a trading system.</p>
      <p className="mt-1 text-muted-foreground">
        Every value is used only from the time it could have been known: PortWatch history is treated as published with
        its weekly Tuesday release plus a 7-day safety margin, prices from the day after the close, and only first
        releases are used for history. Backtests are walk-forward with costs. Nothing here is investment advice, and
        results that look significant after many trials are expected by chance.
      </p>
    </div>
  );
}

export function ResearchView() {
  const [sel, setSel] = useSelection();
  const [picked, setPicked] = useState<string | null>(null);
  const experiments = useExperiments();
  const selected = picked ?? experiments.data?.[0]?.id ?? null;
  return (
    <div className="flex flex-col gap-6">
      <MethodologyNote />
      <Card>
        <CardHeader>
          <CardTitle>Indicator explorer</CardTitle>
          <CardDescription>
            Maritime activity from IMF PortWatch (estimates from satellite AIS) against daily energy prices from FRED.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Explorer sel={sel} onChange={setSel} />
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Experiments</CardTitle>
          <CardDescription>
            A hypothesis, a fixed configuration, and reproducible walk-forward backtests with a buy-and-hold benchmark.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-6">
          <ExperimentsTable selected={selected} onSelect={setPicked} />
          {selected ? <ExperimentDetailView id={selected} /> : null}
          <div className="border-t pt-4">
            <h3 className="mb-2 text-sm font-semibold">New experiment</h3>
            <NewExperimentForm sel={sel} onCreated={setPicked} />
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
