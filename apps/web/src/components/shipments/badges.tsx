import { AlertTriangle, CheckCircle2, Clock, HelpCircle, XCircle } from "lucide-react";
import { Badge, type BadgeTone } from "@/components/ui/badge";
import type { DelayStatus, ShipmentStatus } from "@/lib/types";

const STATUS: Record<ShipmentStatus, string> = {
  unknown: "Unknown",
  planned: "Planned",
  at_origin: "At origin",
  loaded: "Loaded",
  in_transit: "In transit",
  transshipment: "Transshipment",
  discharged: "Discharged",
  delivered: "Delivered",
};

export function ShipmentStatusBadge({ status }: { status: ShipmentStatus }) {
  const tone: BadgeTone = status === "delivered" ? "success" : status === "unknown" ? "neutral" : "info";
  return <Badge tone={tone}>{STATUS[status]}</Badge>;
}

const DELAY: Record<DelayStatus, { label: string; tone: BadgeTone; icon: typeof Clock }> = {
  unknown: { label: "No plan to compare", tone: "neutral", icon: HelpCircle },
  on_time: { label: "On time", tone: "success", icon: CheckCircle2 },
  delayed: { label: "Delayed", tone: "warning", icon: AlertTriangle },
  arrived_on_time: { label: "Arrived on time", tone: "success", icon: CheckCircle2 },
  arrived_late: { label: "Arrived late", tone: "danger", icon: XCircle },
};

/** Status color always ships with an icon and a label (never color alone). */
export function DelayBadge({ status, hours }: { status: DelayStatus; hours?: number | null }) {
  const d = DELAY[status];
  const showHours = hours != null && Math.abs(hours) >= 1 && status !== "unknown";
  return (
    <Badge tone={d.tone} data-delay={status}>
      <d.icon className="size-3" aria-hidden />
      {d.label}
      {showHours ? <span className="tabular-nums">({formatDelay(hours!)})</span> : null}
    </Badge>
  );
}

export function formatDelay(hours: number): string {
  const sign = hours > 0 ? "+" : hours < 0 ? "−" : "";
  const h = Math.abs(hours);
  return h >= 48 ? `${sign}${(h / 24).toFixed(1)} d` : `${sign}${Math.round(h)} h`;
}

