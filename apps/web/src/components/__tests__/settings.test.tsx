import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { LoginForm, safeNext } from "@/components/auth/login-form";
import { ApiKeysSection, AuditLogSection, MembersSection } from "@/components/settings/settings-view";
import type { Me, Member } from "@/lib/types";
import { mockFetch, renderWithQuery } from "@/test/utils";

const replace = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace }),
  useSearchParams: () => new URLSearchParams("next=/shipments"),
  usePathname: () => "/settings",
}));

const me = (role: Me["role"], permissions: string[]): Me => ({
  user: { id: "u-me", email: "me@example.test", full_name: null, last_login_at: null },
  organization: { id: "o1", name: "Local workspace", slug: "local" },
  role, permissions, auth_method: "session",
}); // prettier-ignore

const ADMIN = ["data:read", "shipments:write", "research:write", "members:manage", "api_keys:manage", "audit:read"];
const members: Member[] = [
  { user_id: "u-me", email: "me@example.test", full_name: null, is_active: true, last_login_at: null, role: "admin", joined_at: "2026-11-27T00:00:00Z" },
  { user_id: "u-own", email: "owner@example.test", full_name: "Olive Owner", is_active: true, last_login_at: null, role: "owner", joined_at: "2026-11-27T00:00:00Z" },
  { user_id: "u-an", email: "an@example.test", full_name: null, is_active: true, last_login_at: null, role: "analyst", joined_at: "2026-11-27T00:00:00Z" },
]; // prettier-ignore

describe("sign-in", () => {
  it("only follows same-site relative redirects", () => {
    expect(safeNext("/shipments?x=1")).toBe("/shipments?x=1");
    expect(safeNext("https://evil.test")).toBe("/dashboard");
    expect(safeNext("//evil.test")).toBe("/dashboard");
    expect(safeNext("/\\evil.test")).toBe("/dashboard");
    expect(safeNext("/login")).toBe("/dashboard");
    expect(safeNext(null)).toBe("/dashboard");
  });

  it("signs in and returns to the requested page", async () => {
    const f = mockFetch(() => ({ body: { role: "viewer", permissions: ["data:read"] } }));
    renderWithQuery(<LoginForm />);
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "a@example.test" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "a long passphrase" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/shipments"));
    expect(f.mock.calls[0]?.[0]).toBe("/api/v1/auth/login");
  });

  it("shows the server's generic error and a rate-limit hint", async () => {
    mockFetch(() => ({ status: 429, body: { error: { code: "rate_limited", message: "Too many", details: null, request_id: null } } }));
    renderWithQuery(<LoginForm />);
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "a@example.test" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/Wait a minute/);
  });
});

describe("settings", () => {
  it("lets admins edit members but not owners or themselves", async () => {
    mockFetch(() => ({ body: members }));
    renderWithQuery(<MembersSection me={me("admin", ADMIN)} />);
    const analyst = await screen.findByTestId("member-an@example.test");
    const select = within(analyst).getByLabelText("Role for an@example.test");
    expect(within(select).queryByRole("option", { name: "owner" })).toBeNull();
    expect(within(screen.getByTestId("member-owner@example.test")).queryByRole("combobox")).toBeNull();
    expect(within(screen.getByTestId("member-me@example.test")).getByText(/you/)).toBeInTheDocument();
    expect(screen.getByRole("form", { name: "Add member" })).toBeInTheDocument();
  });

  it("hides administration from viewers", async () => {
    mockFetch(() => ({ body: members }));
    const viewer = me("viewer", ["data:read"]);
    const { container } = renderWithQuery(
      <>
        <MembersSection me={viewer} />
        <ApiKeysSection me={viewer} />
        <AuditLogSection me={viewer} />
      </>,
    );
    await screen.findByTestId("member-an@example.test");
    expect(screen.queryByRole("form", { name: "Add member" })).toBeNull();
    expect(screen.queryByRole("combobox")).toBeNull();
    expect(container).not.toHaveTextContent("API keys");
    expect(container).not.toHaveTextContent("Audit log");
  });

  it("shows a new API key exactly once", async () => {
    const created = { id: "k1", name: "ci", key_prefix: "ab12cd34", scopes: ["data:read"], created_at: "2026-11-27T00:00:00Z",
                      last_used_at: null, expires_at: null, revoked_at: null, created_by: "me@example.test",
                      key: "cs_ab12cd34_secretsecretsecretsecret", role: "viewer" }; // prettier-ignore
    let listed: unknown[] = [];
    mockFetch(() => ({ body: listed }));
    renderWithQuery(<ApiKeysSection me={me("admin", ADMIN)} />);
    await screen.findByText("No API keys");
    mockFetch((path) => (path === "/api/v1/org/api-keys" && listed.length === 0 ? ((listed = [{ ...created, key: undefined }]), { status: 201, body: created }) : { body: listed }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "ci" } });
    fireEvent.click(screen.getByRole("button", { name: /Create key/ }));
    const notice = await screen.findByTestId("new-api-key");
    expect(notice).toHaveTextContent("cs_ab12cd34_secretsecretsecretsecret");
    fireEvent.click(within(notice).getByRole("button", { name: "Done" }));
    expect(screen.queryByTestId("new-api-key")).toBeNull();
    expect(await screen.findByText("cs_ab12cd34_…")).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent("secretsecret");
  });
});
