import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CarrierComparison } from "@/components/carriers/carrier-comparison";
import { DelayBadge, formatDelay } from "@/components/shipments/badges";
import { ImportPanel } from "@/components/shipments/import-panel";
import { ShipmentView } from "@/components/shipments/shipment-view";
import { TrackForm } from "@/components/shipments/track-form";
import { buildMilestoneRows, estimateHistory } from "@/lib/milestones";
import type { ShipmentDetail, ShipmentEvent } from "@/lib/types";
import { mockFetch, renderWithQuery } from "@/test/utils";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push, replace: vi.fn() }), usePathname: () => "/shipments" }));

let n = 0;
function ev(code: string, classifier: ShipmentEvent["classifier"], at: string, created: string, loc = "NLRTM"): ShipmentEvent {
  n += 1;
  return {
    id: `e${n}`, event_type: code === "ARRI" ? "transport" : "equipment", event_code: code, classifier,
    event_at: at, event_created_at: created, location_unlocode: loc, location_name: null,
    container_number: null, vessel_name: "V1", vessel_imo: null, voyage_number: "1E",
    description: code === "ARRI" ? "Vessel arrival" : code, raw_code: code, ingested_at: created,
  };
} // prettier-ignore

describe("milestone rows", () => {
  const events = [
    ev("ARRI", "PLANNED", "2026-11-28T00:00:00Z", "2026-11-01T00:00:00Z"),
    ev("ARRI", "PLANNED", "2026-11-30T00:00:00Z", "2026-11-10T00:00:00Z"), // re-plan
    ev("ARRI", "ESTIMATED", "2026-12-01T00:00:00Z", "2026-11-20T00:00:00Z"),
    ev("ARRI", "ESTIMATED", "2026-11-29T00:00:00Z", "2026-11-15T00:00:00Z"),
    ev("LOAD", "ACTUAL", "2026-11-02T00:00:00Z", "2026-11-02T00:00:00Z", "SGSIN"),
  ];

  it("keeps the original plan and shows a re-plan separately", () => {
    const arri = buildMilestoneRows(events).find((r) => r.code === "ARRI")!;
    expect(arri.planned).toBe("2026-11-28T00:00:00Z");
    expect(arri.replanned).toBe("2026-11-30T00:00:00Z");
    expect(arri.estimate).toBe("2026-12-01T00:00:00Z"); // most recently issued, not latest time
    expect(arri.varianceHours).toBe(72);
  });

  it("orders rows by time and hides estimates once actual", () => {
    const rows = buildMilestoneRows([...events, ev("ARRI", "ACTUAL", "2026-12-02T00:00:00Z", "2026-12-02T00:00:00Z")]);
    expect(rows.map((r) => r.code)).toEqual(["LOAD", "ARRI"]);
    expect(rows[1]!.estimate).toBeNull();
    expect(rows[1]!.varianceHours).toBe(96);
  });

  it("lists the estimate history in issue order", () => {
    expect(estimateHistory(events, "NLRTM").map((e) => e.event_at)).toEqual([
      "2026-11-29T00:00:00Z",
      "2026-12-01T00:00:00Z",
    ]);
  });
});

describe("DelayBadge", () => {
  it("pairs status with an icon and label", () => {
    render(<DelayBadge status="delayed" hours={60} />);
    expect(screen.getByText("Delayed").closest("[data-delay]")).toHaveTextContent("Delayed(+2.5 d)");
  });
  it("formats delays", () => {
    expect(formatDelay(5.4)).toBe("+5 h");
    expect(formatDelay(-3)).toBe("−3 h");
  });
});

describe("TrackForm", () => {
  beforeEach(() => push.mockReset());

  it("explains why a carrier cannot be tracked", async () => {
    mockFetch((path) => {
      if (path === "/api/v1/carriers") return { body: { items: [{ code: "maersk", name: "Maersk" }, { code: "demo", name: "Demo" }], total: 2, limit: 200, offset: 0 } };
      return { status: 409, body: { error: { code: "carrier_integration_unavailable", message: "Tracking with this carrier is not available (authentication required).", details: { status: "authentication_required", reason: "No credentials configured." }, request_id: "r" } } };
    }); // prettier-ignore
    renderWithQuery(<TrackForm />);
    await screen.findByRole("option", { name: "Maersk" });
    fireEvent.change(screen.getByLabelText("Carrier"), { target: { value: "maersk" } });
    await userEvent.type(screen.getByLabelText(/Container, booking/), "MAEU123456789");
    await userEvent.click(screen.getByRole("button", { name: "Track" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("authentication required). No credentials configured.");
    expect(push).not.toHaveBeenCalled();
  });

  it("tracks a demo reference and opens it", async () => {
    const fetchMock = mockFetch((path) => {
      if (path === "/api/v1/carriers") return { body: { items: [{ code: "demo", name: "Demo" }], total: 1, limit: 200, offset: 0 } };
      return { body: { id: "s1" } };
    }); // prettier-ignore
    renderWithQuery(<TrackForm />);
    await userEvent.click(screen.getByRole("button", { name: "Try a demo reference" }));
    expect((screen.getByLabelText(/Container, booking/) as HTMLInputElement).value).toMatch(/^DEMO\d{6}$/);
    await userEvent.click(screen.getByRole("button", { name: "Track" }));
    await vi.waitFor(() => expect(push).toHaveBeenCalledWith("/shipments/s1"));
    const post = fetchMock.mock.calls.find((c) => String(c[0]).endsWith("/shipments/track"));
    expect(post).toBeDefined();
  });
});

describe("ImportPanel", () => {
  it("reports row errors and that nothing was imported", async () => {
    mockFetch(() => ({ status: 422, body: { ok: false, rows: 2, shipments_created: 0, shipments_updated: 0, events_inserted: 0, events_duplicate: 0,
                                             errors: [{ row: 2, field: "container_number", message: "'X' fails the ISO 6346 check digit" }] } })); // prettier-ignore
    renderWithQuery(<ImportPanel />);
    const file = new File(["carrier_code\n"], "s.csv", { type: "text/csv" });
    await userEvent.upload(screen.getByLabelText("CSV file"), file);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Nothing was imported");
    expect(alert).toHaveTextContent("Row 2, container_number: 'X' fails the ISO 6346 check digit");
  });

  it("confirms a successful import", async () => {
    mockFetch(() => ({ body: { ok: true, rows: 3, shipments_created: 1, shipments_updated: 0, events_inserted: 3, events_duplicate: 0, errors: [] } }));
    renderWithQuery(<ImportPanel />);
    await userEvent.upload(screen.getByLabelText("CSV file"), new File(["x"], "s.csv", { type: "text/csv" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Imported 3 event(s): 1 new and 0 updated shipment(s)");
  });
});

const detail = (over: Partial<ShipmentDetail> = {}): ShipmentDetail => ({
  id: "s1", carrier: { code: "demo", name: "Demo" }, reference_type: "booking", reference_value: "DEMO000001",
  origin: { unlocode: "CNSHG", port_name: "Shanghai Pt", mapped: true },
  destination: { unlocode: "PHMNL", port_name: null, mapped: false },
  status: "in_transit", planned_arrival_at: "2026-11-28T00:00:00Z", current_eta_at: "2026-11-30T00:00:00Z",
  actual_arrival_at: null, delay_hours: 48, delay_status: "delayed", last_event_at: "2026-11-20T00:00:00Z",
  last_refreshed_at: "2026-11-26T00:00:00Z", containers: ["ZZZU1234560"],
  provenance: { source: "carrier-demo", data_mode: "synthetic" },
  events: [ev("ARRI", "PLANNED", "2026-11-28T00:00:00Z", "2026-11-01T00:00:00Z", "PHMNL")],
  legs: [{ sequence: 1, carrier_vessel_name: "SYNTHETIC DEMO EXPRESS", carrier_vessel_imo: null, carrier_voyage_number: "DM101E",
           load_unlocode: "CNSHG", discharge_unlocode: "PHMNL", vessel_id: null, vessel_position: null }],
  integration_status: "demo_data_only", refreshable: true, ...over,
}); // prettier-ignore

describe("ShipmentView", () => {
  it("labels synthetic data and explains unmatched vessel legs", async () => {
    mockFetch(() => ({ body: detail() }));
    renderWithQuery(<ShipmentView id="s1" />);
    expect(await screen.findByText("Synthetic shipment")).toBeInTheDocument();
    expect(screen.getByText(/Carrier reported no IMO, so this leg cannot be matched to AIS/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Refresh from carrier/ })).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "Milestones" })).toHaveTextContent("Vessel arrival");
  });

  it("does not offer refresh for imported shipments", async () => {
    mockFetch(() => ({ body: detail({ refreshable: false, provenance: { source: "csv-import", data_mode: "historical" } }) }));
    renderWithQuery(<ShipmentView id="s1" />);
    await screen.findByRole("table", { name: "Milestones" });
    expect(screen.queryByRole("button", { name: /Refresh/ })).not.toBeInTheDocument();
    expect(screen.queryByText("Synthetic shipment")).not.toBeInTheDocument();
  });
});

describe("CarrierComparison", () => {
  it("shows honest integration states and no rate without arrivals", async () => {
    mockFetch(() => ({
      body: [
        { code: "maersk", name: "Maersk", carrier_type: "ocean_carrier", integration_status: "authentication_required", integration_reason: "No credentials",
          last_call_at: null, last_call_outcome: null, vessels_associated: 12, shipments_tracked: 0, shipments_active: 0, shipments_delayed: 0,
          arrivals: 0, on_time_rate: null, avg_arrival_delay_hours: null },
        { code: "demo", name: "Demo", carrier_type: "ocean_carrier", integration_status: "demo_data_only", integration_reason: null,
          last_call_at: null, last_call_outcome: null, vessels_associated: 0, shipments_tracked: 4, shipments_active: 3, shipments_delayed: 1,
          arrivals: 1, on_time_rate: 1, avg_arrival_delay_hours: 2 },
      ],
    })); // prettier-ignore
    renderWithQuery(<CarrierComparison />);
    const maersk = await screen.findByTestId("carrier-row-maersk");
    expect(within(maersk).getByText("Authentication required")).toBeInTheDocument();
    expect(within(maersk).getAllByText("—").length).toBeGreaterThanOrEqual(2);
    const demo = screen.getByTestId("carrier-row-demo");
    expect(within(demo).getByText("Synthetic")).toBeInTheDocument();
    expect(within(demo).getByText(/100%/)).toBeInTheDocument();
  });
});
