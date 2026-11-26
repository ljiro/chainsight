import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { Card } from "@/components/ui/card";

/**
 * A metric tile. `value === null` means "no data source can produce this yet", which is
 * rendered as an explicit explanation, never as 0.
 */
export function KpiCard({
  label,
  value,
  icon: Icon,
  unavailableReason,
  tag,
}: {
  label: string;
  value: number | null;
  icon: LucideIcon;
  unavailableReason: string;
  tag?: ReactNode;
}) {
  return (
    <Card className="p-4">
      <div className="flex items-center justify-between">
        <p className="text-xs font-medium text-muted-foreground">{label}</p>
        <Icon className="size-4 text-muted-foreground" aria-hidden />
      </div>
      {value === null ? (
        <>
          <p className="mt-2 text-2xl font-semibold text-muted-foreground/50" aria-label={`${label}: no data`}>
            —
          </p>
          <p className="mt-1 text-[11px] text-muted-foreground">{unavailableReason}</p>
        </>
      ) : (
        <div className="mt-2 flex items-center gap-2">
          <p className="text-2xl font-semibold tabular-nums">{value.toLocaleString()}</p>
          {tag}
        </div>
      )}
    </Card>
  );
}
