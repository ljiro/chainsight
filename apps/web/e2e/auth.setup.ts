import { expect, test as setup } from "@playwright/test";

// Sign in once and reuse the session: the API allows 10 sign-ins per minute per account.
export const OWNER_STATE = "e2e/.auth/owner.json";

setup("sign in as the owner", async ({ page }) => {
  const email = process.env.E2E_EMAIL ?? "owner@chainsight.local";
  const password = process.env.E2E_PASSWORD;
  if (!password) throw new Error("Set E2E_PASSWORD (and E2E_EMAIL) for an owner account");
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL("**/dashboard");
  await expect(page.getByTestId("user-menu")).toContainText(email);
  await page.context().storageState({ path: OWNER_STATE });
});
