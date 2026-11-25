import { AlertTriangle } from "lucide-react";
import type { ReactNode } from "react";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api";

export function LoadingRows({ rows = 3 }: { rows?: number }) {
  return (
    <div role="status" aria-label="Loading" className="flex flex-col gap-2">
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className="h-8 w-full" />
      ))}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const message = error instanceof Error ? error.message : "Something went wrong";
  const requestId = error instanceof ApiError ? error.requestId : null;
  return (
    <div role="alert" className="flex items-start gap-3 rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm">
      <AlertTriangle className="mt-0.5 size-4 shrink-0 text-destructive" aria-hidden />
      <div className="flex-1">
        <p className="font-medium text-destructive">{message}</p>
        {requestId ? <p className="mt-0.5 font-mono text-[11px] text-muted-foreground">request {requestId}</p> : null}
      </div>
      {onRetry ? (
        <button type="button" onClick={onRetry} className="text-xs font-medium text-primary hover:underline">
          Retry
        </button>
      ) : null}
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-md border border-dashed p-6 text-center">
      <p className="text-sm font-medium">{title}</p>
      {children ? <div className="mt-1 text-xs text-muted-foreground">{children}</div> : null}
    </div>
  );
}
