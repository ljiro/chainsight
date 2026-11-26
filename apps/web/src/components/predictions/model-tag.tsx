import { FlaskConical } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

/** Model provenance shown next to every prediction: exact version, plus a label when the
 *  model learned from synthetic data. */
export function ModelTag({
  version,
  synthetic,
  className,
}: {
  version: string;
  synthetic: boolean;
  className?: string;
}) {
  return (
    <span className={cn("inline-flex flex-wrap items-center gap-1.5", className)}>
      <span className="font-mono text-[11px] text-muted-foreground" title="Model version">
        {version}
      </span>
      {synthetic ? <SyntheticModelBadge /> : null}
    </span>
  );
}

export function SyntheticModelBadge() {
  return (
    <Badge
      tone="synthetic"
      title="This model was trained on synthetic data. Its outputs are illustrative, not evidence."
    >
      <FlaskConical className="size-3" aria-hidden />
      Synthetic-trained
    </Badge>
  );
}
