"use client";

import { Loader2, Search } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api";
import { useCarriers, useTrackShipment } from "@/lib/queries";

function describeError(error: unknown): string {
  if (!(error instanceof ApiError)) return "Tracking failed";
  if (error.code === "carrier_integration_unavailable") {
    const d = error.details as { reason?: string } | null;
    return `${error.message} ${d?.reason ?? ""}`.trim();
  }
  return error.message;
}

export function TrackForm() {
  const router = useRouter();
  const carriers = useCarriers();
  const track = useTrackShipment();
  const [carrier, setCarrier] = useState("demo");
  const [reference, setReference] = useState("");
  const [referenceType, setReferenceType] = useState("");

  const submit = (e: FormEvent) => {
    e.preventDefault();
    track.mutate(
      { carrier, reference, ...(referenceType ? { reference_type: referenceType } : {}) },
      { onSuccess: (s) => router.push(`/shipments/${s.id}`) },
    );
  };

  const fillDemo = () => {
    setCarrier("demo");
    setReference(`DEMO${String(Math.floor(Math.random() * 1_000_000)).padStart(6, "0")}`);
  };

  return (
    <form onSubmit={submit} className="flex flex-col gap-2" aria-label="Track a shipment">
      <div className="flex flex-wrap items-end gap-2">
        <label className="flex flex-col gap-1 text-xs text-muted-foreground">
          Carrier
          <select
            value={carrier}
            onChange={(e) => setCarrier(e.target.value)}
            className="h-9 w-56 rounded-md border bg-card px-2 text-sm text-foreground"
          >
            {(carriers.data?.items ?? [{ code: "demo", name: "ChainSight Demo Carrier (synthetic)" }]).map((c) => (
              <option key={c.code} value={c.code}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-1 flex-col gap-1 text-xs text-muted-foreground">
          Container, booking or B/L number
          <input
            value={reference}
            onChange={(e) => setReference(e.target.value)}
            required
            minLength={3}
            placeholder="e.g. MSKU9070323 or DEMO123456"
            className="h-9 min-w-48 rounded-md border bg-card px-3 text-sm text-foreground"
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-muted-foreground">
          Type
          <select
            value={referenceType}
            onChange={(e) => setReferenceType(e.target.value)}
            className="h-9 rounded-md border bg-card px-2 text-sm text-foreground"
          >
            <option value="">Auto-detect</option>
            <option value="container">Container</option>
            <option value="booking">Booking</option>
            <option value="bill_of_lading">Bill of lading</option>
          </select>
        </label>
        <Button type="submit" disabled={track.isPending}>
          {track.isPending ? <Loader2 className="size-4 animate-spin" /> : <Search className="size-4" />}
          Track
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">
        No carrier credentials yet?{" "}
        <button type="button" onClick={fillDemo} className="font-medium text-primary hover:underline">
          Try a demo reference
        </button>{" "}
        (synthetic data from the demo carrier).
      </p>
      {track.error ? (
        <p role="alert" className="rounded-md border border-destructive/30 bg-destructive/5 p-2 text-sm text-destructive">
          {describeError(track.error)}
        </p>
      ) : null}
    </form>
  );
}
