import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AppSidebar } from "@/components/app-sidebar";

const pathname = vi.hoisted(() => ({ current: "/dashboard" }));
vi.mock("next/navigation", () => ({ usePathname: () => pathname.current }));

describe("AppSidebar", () => {
  beforeEach(() => {
    pathname.current = "/dashboard";
  });

  it("links to every top-level route", () => {
    render(<AppSidebar />);
    const nav = screen.getByRole("navigation", { name: "Main" });
    const hrefs = within(nav)
      .getAllByRole("link")
      .map((a) => a.getAttribute("href"));
    expect(hrefs).toEqual([
      "/dashboard",
      "/fleet",
      "/shipments",
      "/carriers",
      "/predictions",
      "/research",
      "/settings",
    ]);
  });

  it("marks exactly the current route as active", () => {
    pathname.current = "/carriers";
    render(<AppSidebar />);
    const current = screen.getAllByRole("link").filter((a) => a.getAttribute("aria-current") === "page");
    expect(current).toHaveLength(1);
    expect(current[0]).toHaveTextContent("Carriers");
  });

  it("treats nested paths as part of their section", () => {
    pathname.current = "/shipments/abc";
    render(<AppSidebar />);
    expect(screen.getByRole("link", { name: "Shipments" })).toHaveAttribute("aria-current", "page");
  });
});
