import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("next-themes", () => ({ useTheme: () => ({ resolvedTheme: "light" }) }));
// WebGL is unavailable in jsdom; the layout contract is what this test checks.
vi.mock("maplibre-gl", () => ({ default: { Map: vi.fn(), NavigationControl: vi.fn(), Popup: vi.fn() } }));

describe("FleetMap layout", () => {
  it("lets the caller's absolute positioning fill the parent (regression: 0 px tall map)", async () => {
    const { FleetMap } = await import("@/components/fleet/fleet-map");
    render(<FleetMap className="absolute inset-0" vessels={[]} />);
    const wrapper = screen.getByTestId("fleet-map").parentElement!;
    expect(wrapper.className).toContain("absolute");
    expect(wrapper.className).not.toMatch(/\brelative\b/);
    expect(wrapper.style.position).toBe("");
  });

  it("defaults to relative positioning when given an explicit size", async () => {
    const { FleetMap } = await import("@/components/fleet/fleet-map");
    render(<FleetMap className="h-80" vessels={[]} />);
    const wrapper = screen.getByTestId("fleet-map").parentElement!;
    expect(wrapper.className).toContain("relative");
    expect(wrapper.className).toContain("h-80");
  });
});
