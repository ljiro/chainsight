import { expect, test } from "@playwright/test";

const email = process.env.E2E_EMAIL ?? "owner@chainsight.local";
const password = process.env.E2E_PASSWORD ?? "";
const anonymous = { cookies: [], origins: [] };

test.describe("signed out", () => {
  test.use({ storageState: anonymous });

  test("anonymous visitors are sent to sign-in and the API refuses them", async ({ page, request }) => {
    await page.goto("/shipments");
    await page.waitForURL(/\/login\?next=%2Fshipments/);
    await expect(page.getByRole("heading", { name: /Sign in to ChainSight/ })).toBeVisible();
    expect((await request.get("/api/v1/shipments")).status()).toBe(401);
  });

  test("a wrong password shows one generic error", async ({ page }) => {
    await page.goto("/login");
    await page.getByLabel("Email").fill(email);
    await page.getByLabel("Password").fill("definitely not the password");
    await page.getByRole("button", { name: "Sign in" }).click();
    // Scoped to the form: Next.js also renders a (route announcer) role=alert element.
    await expect(page.getByRole("form", { name: "Sign in" }).getByRole("alert")).toContainText(/incorrect|Too many/);
  });
});

test.describe("signed in", () => {
  test("every main page renders", async ({ page }) => {
    const failures: string[] = [];
    page.on("response", (r) => {
      if (r.url().includes("/api/v1/") && r.status() >= 500) failures.push(`${r.status()} ${r.url()}`);
    });
    for (const [path, heading] of [
      ["/dashboard", /Operations overview/],
      ["/fleet", /fleet map/],
      ["/shipments", /Shipment tracking/],
      ["/carriers", /Carrier intelligence/],
      ["/predictions", /Predictive intelligence/],
      ["/research", /Quantitative maritime research/],
      ["/settings", /Settings/],
    ] as const) {
      await page.goto(path);
      await expect(page.getByRole("heading", { level: 1, name: heading })).toBeVisible();
      await expect(page.getByText("Cannot reach the ChainSight API")).toHaveCount(0);
    }
    await expect(page.getByTestId("user-menu")).toContainText(email);
    expect(failures).toEqual([]);
  });

  test("track a demo shipment", async ({ page }) => {
    await page.goto("/shipments");
    const reference = `DEMO${String(Date.now()).slice(-7)}`;
    const form = page.getByRole("form", { name: "Track a shipment" });
    await form.getByPlaceholder(/DEMO123456/).fill(reference);
    await form.getByRole("button", { name: /Track/ }).click();
    await expect(page.getByText(reference).first()).toBeVisible();
  });

  test("an admin can issue an API key once and revoke it", async ({ page, request }) => {
    await page.goto("/settings");
    const name = `e2e ${Date.now()}`;
    const form = page.getByRole("form", { name: "Create API key" });
    await form.getByLabel("Name").fill(name);
    await form.getByRole("button", { name: /Create key/ }).click();
    const notice = page.getByTestId("new-api-key");
    const key = (await notice.locator("code").first().textContent())?.trim() ?? "";
    expect(key).toMatch(/^cs_[0-9a-f]{8}_/);

    const ok = await request.get("/api/v1/auth/me", { headers: { "X-API-Key": key } });
    expect(ok.status()).toBe(200);
    expect((await ok.json()).auth_method).toBe("api_key");

    await notice.getByRole("button", { name: "Done" }).click();
    await expect(page.getByTestId("new-api-key")).toHaveCount(0);
    page.once("dialog", (d) => void d.accept());
    const row = page.getByRole("table", { name: "API keys" }).getByRole("row", { name: new RegExp(name) });
    await row.getByRole("button", { name: "Revoke" }).click();
    await expect(row).toContainText("revoked");
    expect((await request.get("/api/v1/auth/me", { headers: { "X-API-Key": key } })).status()).toBe(401);
    await page.reload();
    await expect(page.getByRole("table", { name: "Audit log" }).getByText("api_key.revoked").first()).toBeVisible();
  });
});

test.describe("sign-out", () => {
  // A fresh session, so revoking it does not affect the shared one.
  test.use({ storageState: anonymous });

  test("signing out ends the session", async ({ page }) => {
    await page.goto("/login");
    await page.getByLabel("Email").fill(email);
    await page.getByLabel("Password").fill(password);
    await page.getByRole("button", { name: "Sign in" }).click();
    await page.waitForURL("**/dashboard");
    await page.getByRole("button", { name: "Sign out" }).click();
    await page.waitForURL(/\/login/);
    expect((await page.request.get("/api/v1/auth/me")).status()).toBe(401);
    await page.goto("/dashboard");
    await page.waitForURL(/\/login/);
  });
});
