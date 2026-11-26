import type { ShipmentEvent } from "./types";

export interface MilestoneRow {
  key: string;
  code: string;
  description: string;
  location: string | null;
  locationName: string | null;
  vessel: string | null;
  planned: string | null; // original (earliest-issued) plan
  replanned: string | null; // latest plan, when it differs from the original
  estimate: string | null; // most recently issued estimate
  actual: string | null;
  varianceHours: number | null; // actual (or estimate) minus original plan
}

const issued = (e: ShipmentEvent) => new Date(e.event_created_at ?? e.ingested_at).getTime();
const time = (e: ShipmentEvent) => new Date(e.event_at).getTime();

/**
 * One row per (milestone code, location), with planned / estimated / actual side by side.
 * Planned is the earliest-issued plan; a later re-plan is shown separately, never replacing it.
 */
export function buildMilestoneRows(events: ShipmentEvent[]): MilestoneRow[] {
  const groups = new Map<string, ShipmentEvent[]>();
  for (const e of events) {
    const key = `${e.event_code}|${e.location_unlocode ?? ""}|${e.voyage_number ?? ""}`;
    groups.set(key, [...(groups.get(key) ?? []), e]);
  }
  const rows: MilestoneRow[] = [];
  for (const [key, group] of groups) {
    const plans = group.filter((e) => e.classifier === "PLANNED").sort((a, b) => issued(a) - issued(b));
    const ests = group.filter((e) => e.classifier === "ESTIMATED").sort((a, b) => issued(a) - issued(b));
    const acts = group.filter((e) => e.classifier === "ACTUAL").sort((a, b) => time(a) - time(b));
    const first = group[0]!;
    const planned = plans[0]?.event_at ?? null;
    const latestPlan = plans.at(-1)?.event_at ?? null;
    const estimate = ests.at(-1)?.event_at ?? null;
    const actual = acts.at(-1)?.event_at ?? null;
    const compare = actual ?? estimate;
    rows.push({
      key,
      code: first.event_code,
      description: first.description ?? first.event_code,
      location: first.location_unlocode,
      locationName: group.find((e) => e.location_name)?.location_name ?? null,
      vessel: group.find((e) => e.vessel_name)?.vessel_name ?? null,
      planned,
      replanned: latestPlan !== planned ? latestPlan : null,
      estimate: actual ? null : estimate,
      actual,
      varianceHours:
        planned && compare ? Math.round((new Date(compare).getTime() - new Date(planned).getTime()) / 36e5) : null,
    });
  }
  const anchor = (r: MilestoneRow) => new Date(r.planned ?? r.actual ?? r.estimate ?? 0).getTime();
  return rows.sort((a, b) => anchor(a) - anchor(b));
}

/** Arrival estimates for the destination in the order they were issued (append-only history). */
export function estimateHistory(events: ShipmentEvent[], destination: string | null): ShipmentEvent[] {
  return events
    .filter((e) => e.classifier === "ESTIMATED" && e.event_code === "ARRI" && e.location_unlocode === destination)
    .sort((a, b) => issued(a) - issued(b));
}
