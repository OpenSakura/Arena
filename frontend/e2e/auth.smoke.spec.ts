import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

const AUTHENTIK_USERNAME = "akadmin";
const AUTHENTIK_PASSWORD = "password1234";

async function hasAccessToken(baseUrl: string, page: Page): Promise<boolean> {
  const response = await page.context().request.get(`${baseUrl}/api/auth/session`);
  if (!response.ok()) {
    return false;
  }
  const payload = await response.json();
  return Boolean(
    payload &&
      typeof payload === "object" &&
      "accessToken" in payload &&
      typeof payload.accessToken === "string" &&
      payload.accessToken.length > 0,
  );
}

test("login and logout through Authentik", async ({ baseURL, page }) => {
  if (!baseURL) {
    throw new Error("Playwright baseURL is required");
  }

  await page.goto("/");
  await page.getByRole("button", { name: "Login" }).click();

  const authForm = page.getByRole("main", { name: /authentication form/i });

  const userInput = authForm.locator('input[name="uidField"]');
  await userInput.waitFor({ state: "visible", timeout: 60_000 });
  await userInput.fill(AUTHENTIK_USERNAME);
  await userInput.press("Enter");

  const identifyAction = authForm.getByRole("button", { name: /log in|continue/i });
  await identifyAction.waitFor({ state: "visible", timeout: 30_000 });
  await identifyAction.click({ force: true });

  const passwordInput = authForm.locator('input[name="password"]');
  await passwordInput.waitFor({ state: "visible", timeout: 60_000 });
  await passwordInput.fill(AUTHENTIK_PASSWORD);
  await expect(passwordInput).toHaveValue(AUTHENTIK_PASSWORD);
  await passwordInput.press("Enter");

  const passwordAction = authForm.getByRole("button", { name: /log in|continue/i });
  await passwordAction.waitFor({ state: "visible", timeout: 30_000 });
  await passwordAction.click({ force: true });

  await expect
    .poll(async () => hasAccessToken(baseURL, page), {
      timeout: 90_000,
      message: "Expected NextAuth session to include accessToken after login",
    })
    .toBe(true);

  await page.goto("/");
  const logoutButton = page.getByRole("button", { name: "Logout" });
  await logoutButton.waitFor({ state: "visible", timeout: 60_000 });
  await logoutButton.click();

  await expect
    .poll(async () => hasAccessToken(baseURL, page), {
      timeout: 60_000,
      message: "Expected NextAuth session accessToken to clear after logout",
    })
    .toBe(false);

  await page.goto("/");
  await page.getByRole("button", { name: "Login" }).waitFor({ state: "visible", timeout: 60_000 });
});
