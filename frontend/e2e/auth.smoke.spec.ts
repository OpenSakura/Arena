import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

const AUTHENTIK_USERNAME = "akadmin";
const AUTHENTIK_PASSWORD = "password1234";

async function waitForAuthentikLogin(page: Page) {
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
}

test("login and logout through the SPA OIDC flow", async ({ page }) => {
  await page.route("**/api/v1/public-config", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        anon_battle_turnstile_required: false,
        oidc: {
          issuer: "http://localhost:29000/application/o/arena-e2e/",
          client_id: "arena-e2e-client",
          scope: "openid profile email offline_access",
          redirect_path: "/auth/callback",
          silent_redirect_path: "/auth/silent-callback",
          post_logout_redirect_path: "/auth/logout-callback",
        },
      }),
    });
  });

  await page.goto("/");
  await page.getByRole("button", { name: "Login" }).click();

  await waitForAuthentikLogin(page);

  await page.waitForURL((url) => url.pathname === "/", { timeout: 90_000 });
  await expect(page.getByRole("button", { name: "Logout" })).toBeVisible({ timeout: 60_000 });
  await expect(page.getByRole("button", { name: "Login" })).toBeHidden();

  const logoutButton = page.getByRole("button", { name: "Logout" });
  await logoutButton.waitFor({ state: "visible", timeout: 60_000 });
  await logoutButton.click();

  const returnToArenaLink = page.getByRole("link", { name: /Log back into Arena E2E/i });
  await returnToArenaLink.waitFor({ state: "visible", timeout: 90_000 });
  await returnToArenaLink.click();

  await page.waitForURL((url) => url.pathname === "/", { timeout: 90_000 });
  await expect(page.getByRole("button", { name: "Login" })).toBeVisible({ timeout: 60_000 });
  await expect(page.getByRole("button", { name: "Logout" })).toBeHidden();
});
