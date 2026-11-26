"use client";

import { Search } from "lucide-react";
import { CLASS_GROUPS, GROUP_COLORS, type ClassGroup, type FleetFilterState } from "@/lib/fleet";
import { cn } from "@/lib/utils";

export function FleetFilters({
  value,
  onChange,
  carriers,
  mode,
}: {
  value: FleetFilterState;
  onChange: (next: FleetFilterState) => void;
  carriers: { code: string; name: string }[];
  mode: "light" | "dark";
}) {
  const toggle = (g: ClassGroup) =>
    onChange({
      ...value,
      groups: value.groups.includes(g) ? value.groups.filter((x) => x !== g) : [...value.groups, g],
    });

  return (
    <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Fleet filters">
      <label className="relative">
        <span className="sr-only">Search vessels</span>
        <Search className="pointer-events-none absolute top-2 left-2.5 size-4 text-muted-foreground" aria-hidden />
        <input
          type="search"
          value={value.query}
          onChange={(e) => onChange({ ...value, query: e.target.value })}
          placeholder="Name or MMSI"
          className="h-8 w-48 rounded-md border bg-card pr-2 pl-8 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
      </label>

      <div className="flex flex-wrap gap-1" aria-label="Vessel class">
        {CLASS_GROUPS.map((g) => {
          const active = value.groups.length === 0 || value.groups.includes(g.key);
          return (
            <button
              key={g.key}
              type="button"
              aria-pressed={value.groups.includes(g.key)}
              onClick={() => toggle(g.key)}
              className={cn(
                "flex h-8 items-center gap-1.5 rounded-md border px-2.5 text-xs transition-colors",
                value.groups.includes(g.key) ? "bg-accent font-medium" : "bg-card hover:bg-accent/60",
                !active && "opacity-50",
              )}
            >
              <span aria-hidden className="size-2.5 rounded-full" style={{ background: GROUP_COLORS[mode][g.key] }} />
              {g.label}
            </button>
          );
        })}
      </div>

      <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
        Carrier
        <select
          value={value.carrier ?? ""}
          onChange={(e) => onChange({ ...value, carrier: e.target.value || null })}
          className="h-8 w-40 rounded-md border bg-card px-2 text-sm text-foreground"
        >
          <option value="">All</option>
          {carriers.map((c) => (
            <option key={c.code} value={c.code}>
              {c.name}
            </option>
          ))}
        </select>
      </label>

      <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
        Seen
        <select
          value={value.freshness}
          onChange={(e) => onChange({ ...value, freshness: e.target.value as FleetFilterState["freshness"] })}
          className="h-8 rounded-md border bg-card px-2 text-sm text-foreground"
        >
          <option value="all">Last 24 h</option>
          <option value="recent">Last hour</option>
          <option value="live">Last 10 min</option>
        </select>
      </label>
    </div>
  );
}
