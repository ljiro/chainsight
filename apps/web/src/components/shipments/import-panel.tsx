"use client";

import { Download, Loader2, Upload } from "lucide-react";
import { useRef } from "react";
import { Button } from "@/components/ui/button";
import { useImportShipments } from "@/lib/queries";

export const CSV_TEMPLATE = `carrier_code,reference_type,reference_value,event_code,classifier,event_at,location_unlocode,container_number,vessel_name,vessel_imo,voyage_number,origin_unlocode,destination_unlocode,description
msc,booking,MYBOOKING001,GTIN,ACTUAL,2026-11-01T08:00:00Z,SGSIN,MSKU9070323,,,,SGSIN,NLRTM,
msc,booking,MYBOOKING001,LOAD,ACTUAL,2026-11-02T08:00:00Z,SGSIN,MSKU9070323,,,,,,
msc,booking,MYBOOKING001,ARRI,PLANNED,2026-11-28T00:00:00Z,NLRTM,,,,,,,
`;

/** Read a file as text; falls back to FileReader where Blob.text() is unavailable. */
export function readFileText(file: Blob): Promise<string> {
  if (typeof file.text === "function") return file.text();
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(reader.error);
    reader.readAsText(file);
  });
}

export function ImportPanel() {
  const input = useRef<HTMLInputElement>(null);
  const importer = useImportShipments();
  const report = importer.data;

  const onFile = async (file: File | undefined) => {
    if (!file) return;
    importer.mutate(await readFileText(file));
    if (input.current) input.current.value = "";
  };

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <input
          ref={input}
          type="file"
          accept=".csv,text/csv"
          aria-label="CSV file"
          className="sr-only"
          onChange={(e) => void onFile(e.target.files?.[0])}
        />
        <Button type="button" variant="outline" onClick={() => input.current?.click()} disabled={importer.isPending}>
          {importer.isPending ? <Loader2 className="size-4 animate-spin" /> : <Upload className="size-4" />}
          Import CSV
        </Button>
        <a
          href={`data:text/csv;charset=utf-8,${encodeURIComponent(CSV_TEMPLATE)}`}
          download="chainsight-shipments-template.csv"
          className="inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline"
        >
          <Download className="size-3.5" aria-hidden /> Template
        </a>
      </div>
      <p className="text-xs text-muted-foreground">
        One row per event (DCSA codes such as GTIN, LOAD, DEPA, ARRI, DISC, GTOT). Timestamps need a timezone. The
        whole file is rejected if any row is invalid. Imported data is labeled as historical.
      </p>
      {importer.error ? (
        <p role="alert" className="text-sm text-destructive">
          {importer.error instanceof Error ? importer.error.message : "Import failed"}
        </p>
      ) : null}
      {report ? (
        report.ok ? (
          <p role="status" className="rounded-md border border-success/30 bg-success/10 p-2 text-sm">
            Imported {report.events_inserted} event(s): {report.shipments_created} new and {report.shipments_updated}{" "}
            updated shipment(s){report.events_duplicate ? `, ${report.events_duplicate} duplicate(s) skipped` : ""}.
          </p>
        ) : (
          <div role="alert" className="rounded-md border border-destructive/30 bg-destructive/5 p-2 text-sm">
            <p className="font-medium text-destructive">Nothing was imported: fix these rows and try again.</p>
            <ul className="mt-1 max-h-40 overflow-y-auto text-xs">
              {report.errors.slice(0, 50).map((e) => (
                <li key={`${e.row}-${e.field}`}>
                  Row {e.row}, <span className="font-mono">{e.field}</span>: {e.message}
                </li>
              ))}
            </ul>
          </div>
        )
      ) : null}
    </div>
  );
}
