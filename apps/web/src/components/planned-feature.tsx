import { Construction } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

/**
 * Honest placeholder for functionality that is not built yet. It never renders sample numbers,
 * so nothing on screen can be mistaken for real data.
 */
export function PlannedFeature({
  title,
  phase,
  description,
  items,
}: {
  title: string;
  phase: number;
  description: string;
  items?: string[];
}) {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="flex items-center gap-2">
            <Construction className="size-4 text-muted-foreground" aria-hidden />
            {title}
          </CardTitle>
          <Badge>Planned · Phase {phase}</Badge>
        </div>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      {items?.length ? (
        <CardContent>
          <ul className="grid gap-1.5 text-sm text-muted-foreground sm:grid-cols-2">
            {items.map((item) => (
              <li key={item} className="flex gap-2">
                <span aria-hidden className="mt-2 size-1 shrink-0 rounded-full bg-muted-foreground/60" />
                {item}
              </li>
            ))}
          </ul>
        </CardContent>
      ) : null}
    </Card>
  );
}
