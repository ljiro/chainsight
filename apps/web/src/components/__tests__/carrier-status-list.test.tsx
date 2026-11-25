import { screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { CarrierStatusList } from "@/components/dashboard/carrier-status-list";
import type { Carrier } from "@/lib/types";
import { mockFetch, renderWithQuery } from "@/test/utils";

const carriers: Carrier[] = [
  {
    code: "maersk",
    name: "Maersk",
    scac: "MAEU",
    carrier_type: "ocean_carrier",
    website_url: null,
    integrations: [
      {
        capability: "shipment_tracking",
        status: "unavailable",
        status_reason: "No adapter implemented yet (planned for Phase 3).",
        last_checked_at: null,
        last_success_at: null,
      },
    ],
  },
  {
    code: "dhl",
    name: "DHL Global Forwarding",
    scac: null,
    carrier_type: "logistics_provider",
    website_url: null,
    integrations: [],
  },
];

describe("CarrierStatusList", () => {
  it("renders each carrier with its tracking integration status", async () => {
    mockFetch(() => ({ body: { items: carriers, total: 2, limit: 200, offset: 0 } }));
    renderWithQuery(<CarrierStatusList />);

    const maersk = await screen.findByTestId("carrier-maersk");
    expect(within(maersk).getByText("MAEU")).toBeInTheDocument();
    expect(within(maersk).getByText("Unavailable")).toBeInTheDocument();
    expect(within(maersk).getByText(/No adapter implemented yet/)).toBeInTheDocument();

    const dhl = screen.getByTestId("carrier-dhl");
    expect(within(dhl).getByText(/No SCAC/)).toBeInTheDocument();
    // A carrier with no integration record must never appear connected.
    expect(within(dhl).getByText("Unavailable")).toBeInTheDocument();
  });

  it("hides status reasons in compact mode", async () => {
    mockFetch(() => ({ body: { items: carriers, total: 2, limit: 200, offset: 0 } }));
    renderWithQuery(<CarrierStatusList compact />);
    await screen.findByTestId("carrier-maersk");
    expect(screen.queryByText(/No adapter implemented yet/)).not.toBeInTheDocument();
  });

  it("shows a network error when the API is unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new TypeError("Failed to fetch"))));
    renderWithQuery(<CarrierStatusList />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Cannot reach the ChainSight API");
  });
});
