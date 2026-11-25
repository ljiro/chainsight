import { describe, expect, it, vi } from "vitest";
import { ApiError, apiGet } from "@/lib/api";
import { isActive } from "@/lib/nav";
import { formatAge, humanize } from "@/lib/utils";

const json = (status: number, body: unknown) =>
  vi.fn<typeof fetch>(() => Promise.resolve(new Response(JSON.stringify(body), { status })));

describe("apiGet", () => {
  it("prefixes the versioned API path", async () => {
    const f = json(200, { ok: true });
    vi.stubGlobal("fetch", f);
    await apiGet("/health");
    expect(f.mock.calls[0]?.[0]).toBe("/api/v1/health");
  });

  it("throws ApiError from the error envelope", async () => {
    vi.stubGlobal(
      "fetch",
      json(404, { error: { code: "not_found", message: "Not Found", details: null, request_id: "r1" } }),
    );
    const err = await apiGet("/x").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 404, code: "not_found", requestId: "r1", message: "Not Found" });
  });

  it("returns the body for explicitly accepted statuses (readiness 503)", async () => {
    vi.stubGlobal("fetch", json(503, { status: "error", components: [] }));
    await expect(apiGet("/health/ready", { acceptStatuses: [503] })).resolves.toMatchObject({ status: "error" });
  });

  it("handles non-JSON error bodies", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response("<html>bad gateway</html>", { status: 502 }))));
    await expect(apiGet("/x")).rejects.toMatchObject({ status: 502, code: "http_error" });
  });
});

describe("formatAge", () => {
  const now = new Date("2026-11-25T12:00:00Z");
  it.each([
    [null, "never"],
    ["2026-11-25T11:59:30Z", "30 seconds ago"],
    ["2026-11-25T11:55:00Z", "5 minutes ago"],
    ["2026-11-25T09:00:00Z", "3 hours ago"],
    ["2026-11-23T12:00:00Z", "2 days ago"],
  ])("%s -> %s", (iso, expected) => {
    expect(formatAge(iso, now)).toBe(expected);
  });
});

describe("helpers", () => {
  it("humanize", () => {
    expect(humanize("ocean_carrier")).toBe("Ocean carrier");
  });
  it("isActive does not match sibling prefixes", () => {
    expect(isActive("/research", "/research")).toBe(true);
    expect(isActive("/research/x", "/research")).toBe(true);
    expect(isActive("/researchers", "/research")).toBe(false);
  });
});
