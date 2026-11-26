import { Badge, type BadgeTone } from "@/components/ui/badge";
import type {
  ComponentState,
  DataMode,
  DataSourceStatus,
  Freshness,
  IntegrationStatus,
  PositionFreshness,
} from "@/lib/types";

type Spec = { label: string; tone: BadgeTone };

const INTEGRATION: Record<IntegrationStatus, Spec> = {
  connected: { label: "Connected", tone: "success" },
  authentication_required: { label: "Authentication required", tone: "warning" },
  pending_approval: { label: "Pending approval", tone: "info" },
  unavailable: { label: "Unavailable", tone: "neutral" },
  demo_data_only: { label: "Demo data only", tone: "synthetic" },
};

const SOURCE: Record<DataSourceStatus, Spec> = {
  active: { label: "Active", tone: "success" },
  degraded: { label: "Degraded", tone: "warning" },
  error: { label: "Error", tone: "danger" },
  not_configured: { label: "Not configured", tone: "neutral" },
  disabled: { label: "Disabled", tone: "neutral" },
};

const MODE: Record<DataMode, Spec> = {
  real_time: { label: "Real-time", tone: "info" },
  periodic: { label: "Periodic", tone: "neutral" },
  historical: { label: "Historical", tone: "neutral" },
  synthetic: { label: "Synthetic", tone: "synthetic" },
};

const FRESHNESS: Record<Freshness, Spec> = {
  fresh: { label: "Fresh", tone: "success" },
  stale: { label: "Stale", tone: "warning" },
  never: { label: "No data yet", tone: "neutral" },
  unknown: { label: "No schedule", tone: "neutral" },
};

const POSITION: Record<PositionFreshness, Spec> = {
  live: { label: "Live", tone: "success" },
  recent: { label: "Recent", tone: "info" },
  stale: { label: "Stale", tone: "warning" },
};

const HEALTH: Record<ComponentState, Spec> = {
  ok: { label: "Healthy", tone: "success" },
  degraded: { label: "Degraded", tone: "warning" },
  error: { label: "Down", tone: "danger" },
};

const MAPS = {
  integration: INTEGRATION,
  source: SOURCE,
  mode: MODE,
  freshness: FRESHNESS,
  position: POSITION,
  health: HEALTH,
} as const;

type Kind = keyof typeof MAPS;
type ValueOf<K extends Kind> = keyof (typeof MAPS)[K];

export function StatusBadge<K extends Kind>({ kind, value }: { kind: K; value: ValueOf<K> }) {
  const spec = (MAPS[kind] as Record<string, Spec>)[value as string] ?? {
    label: String(value),
    tone: "neutral" as const,
  };
  return (
    <Badge tone={spec.tone} data-kind={kind} data-value={String(value)}>
      <span aria-hidden className="size-1.5 rounded-full bg-current" />
      {spec.label}
    </Badge>
  );
}
