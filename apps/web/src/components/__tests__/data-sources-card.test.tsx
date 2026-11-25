import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DataSourcesCard } from "@/components/dashboard/data-sources-card";
import type { DataSource, Page } from "@/lib/types";
import { mockFetch, renderWithQuery } from "@/test/utils";

const source = (overrides: Partial<DataSource>): DataSource => ({
  key: "aisstream",
  name: "AISStream",
  category: "ais",
  data_mode: "real_time",
  status: "not_configured",
  homepage_url: "https://aisstream.io",
  license_note: null,
  attribution: null,
  expected_update_interval_seconds: 60,
  last_success_at: null,
  last_error_at: null,
  freshness: "never",
  ...overrides,
});

const page = (items: DataSource[]): Page<DataSource> => ({ items, total: items.length, limit: 200, offset: 0 });

describe("DataSourcesCard", () => {
  it("shows a loading state while fetching", () => {
    mockFetch(() => "pending");
    renderWithQuery(<DataSourcesCard />);
    expect(screen.getByRole("status", { name: "Loading" })).toBeInTheDocument();
  });

  it("renders the backend error envelope message and request id", async () => {
    mockFetch(() => ({
      status: 500,
      body: { error: { code: "internal_error", message: "An unexpected error occurred", details: null, request_id: "rid-42" } },
    }));
    renderWithQuery(<DataSourcesCard />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("An unexpected error occurred");
    expect(alert).toHaveTextContent("rid-42");
    expect(within(alert).getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("renders status, data mode, and freshness for each source", async () => {
    const fetchMock = mockFetch((path) => {
      expect(path).toBe("/api/v1/data-sources");
      return {
        body: page([
          source({}),
          source({ key: "replay", name: "AIS replay", data_mode: "synthetic", status: "active", freshness: "fresh" }),
        ]),
      };
    });
    renderWithQuery(<DataSourcesCard />);

    const ais = await screen.findByTestId("source-aisstream");
    expect(within(ais).getByText("Not configured")).toBeInTheDocument();
    expect(within(ais).getByText("Real-time")).toBeInTheDocument();
    expect(within(ais).getByText("No data yet")).toBeInTheDocument();
    expect(within(ais).getByText("never")).toBeInTheDocument();

    const replay = screen.getByTestId("source-replay");
    expect(within(replay).getByText("Synthetic")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("shows an empty state when no sources are registered", async () => {
    mockFetch(() => ({ body: page([]) }));
    renderWithQuery(<DataSourcesCard />);
    expect(await screen.findByText("No data sources registered")).toBeInTheDocument();
  });
});
