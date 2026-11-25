import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StatusBadge } from "@/components/status-badge";

describe("StatusBadge", () => {
  it.each([
    ["connected", "Connected"],
    ["authentication_required", "Authentication required"],
    ["pending_approval", "Pending approval"],
    ["unavailable", "Unavailable"],
    ["demo_data_only", "Demo data only"],
  ] as const)("labels integration status %s", (value, label) => {
    render(<StatusBadge kind="integration" value={value} />);
    expect(screen.getByText(label)).toBeInTheDocument();
  });

  it("marks synthetic data with the synthetic tone so it can't pass as real", () => {
    render(<StatusBadge kind="mode" value="synthetic" />);
    const badge = screen.getByText("Synthetic").closest("span[data-kind]");
    expect(badge).toHaveAttribute("data-value", "synthetic");
    expect(badge?.className).toContain("text-synthetic");
  });

  it("shows 'No data yet' instead of a timestamp for never-updated sources", () => {
    render(<StatusBadge kind="freshness" value="never" />);
    expect(screen.getByText("No data yet")).toBeInTheDocument();
  });
});
