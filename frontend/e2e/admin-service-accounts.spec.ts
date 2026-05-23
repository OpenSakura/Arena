import { expect, test, type Page } from "@playwright/test";
import * as fs from "node:fs/promises";
import * as path from "node:path";

async function mockAuthenticatedSession(page: Page, accessToken = "frontend-admin-access-token", isAdmin = true): Promise<void> {
  await page.route("**/api/v1/public-config", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        oidc: {
          issuer: "http://localhost:13000/mock-oidc",
          client_id: "arena",
          scope: "openid profile email",
          redirect_path: "/auth/callback",
          silent_redirect_path: "/auth/silent-callback",
          post_logout_redirect_path: "/auth/logout-callback"
        }
      }),
    });
  });

  await page.route(/\/api\/v1\/me$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        authenticated: true,
        is_admin: isAdmin,
      }),
    });
  });

  await page.addInitScript((token) => {
    sessionStorage.setItem("oidc.user:http://localhost:13000/mock-oidc:arena", JSON.stringify({
      access_token: token,
      expires_at: Math.floor(Date.now() / 1000) + 3600,
      token_type: "Bearer",
      scope: "openid profile email",
      profile: { sub: "admin123" }
    }));
  }, accessToken);
}

test("admin service accounts token lifecycle", async ({ page }) => {
  const listCalls: any[] = [];
  const createTokenCalls: any[] = [];
  const revokeTokenCalls: any[] = [];
  
  let accounts = [
    {
      id: "sa-1",
      name: "Playwright Bot",
      description: "For e2e",
      enabled: true,
      scopes: [],
      tokens: [],
      created_at: "2026-02-18T00:00:00Z",
      updated_at: "2026-02-18T00:00:00Z"
    }
  ];

  await mockAuthenticatedSession(page);

  await page.route("**/api/v1/admin/service-accounts", async (route) => {
    if (route.request().method() === "GET") {
      listCalls.push(route.request().url());
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ service_accounts: accounts }),
      });
      return;
    }
    await route.abort();
  });

  await page.route(/\/api\/v1\/admin\/service-accounts\/[^/]+\/tokens$/, async (route) => {
    if (route.request().method() === "POST") {
      const payload = route.request().postDataJSON();
      createTokenCalls.push({
        url: route.request().url(),
        payload
      });

      const { pathname } = new URL(route.request().url());
      const parts = pathname.split("/");
      const saId = parts[parts.length - 2];

      const token = {
        id: "tok-1",
        service_account_id: saId,
        token_prefix: "pt_secret_toke",
        status: "active",
        scopes: payload.scopes,
        created_at: "2026-02-18T00:00:00Z",
        expires_at: null,
        last_used_at: null,
        revoked_at: null,
      };

      accounts = accounts.map(a => {
        if (a.id !== saId) return a;
        return { ...a, tokens: [token, ...a.tokens] };
      });

      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({
          service_account: accounts.find(a => a.id === saId),
          token,
          plaintext_token: "pt_secret_token_123_full"
        }),
      });
      return;
    }
    await route.abort();
  });

  await page.goto("/admin/service-accounts");

  await expect(page.getByText("Playwright Bot")).toBeVisible();
  
  await page.getByRole("button", { name: "Tokens" }).click();
  await page.getByRole("button", { name: "New Token" }).click();
  
  await page.locator('label').filter({ hasText: 'battle:create' }).locator('input').check();
  await page.getByRole("button", { name: "Confirm Create" }).click();

  await expect(page.getByText("Copy now. This token will not be shown again.")).toBeVisible();
  await expect(page.getByText("pt_secret_token_123_full")).toBeVisible();

  await page.waitForTimeout(500); // Wait for UI stabilization
  await page.screenshot({ path: ".omo/evidence/task-10-admin-token-ui.png", fullPage: true });

  await page.getByRole("button", { name: "Dismiss" }).click();
  await expect(page.getByText("pt_secret_token_123_full")).not.toBeVisible();

  await page.goto("/admin/service-accounts");
  await page.getByRole("button", { name: "Tokens" }).click();
  await expect(page.getByText("pt_secret_toke...")).toBeVisible();
  await expect(page.getByText("pt_secret_token_123_full")).not.toBeVisible();
  
  await page.waitForTimeout(500); // Wait for UI stabilization
  await page.screenshot({ path: ".omo/evidence/task-10-token-hidden-after-refresh.png", fullPage: true });

  // Network and storage audit
  const storage = await page.evaluate(() => {
    return {
      localStorage: JSON.stringify(localStorage),
      sessionStorage: JSON.stringify(sessionStorage)
    };
  });

  expect(storage.localStorage).not.toContain("pt_secret_token");
  expect(storage.sessionStorage).not.toContain("pt_secret_token");

  await fs.mkdir(".omo/evidence", { recursive: true });
  await fs.writeFile(".omo/evidence/task-10-token-storage-audit.json", JSON.stringify({
    safe: !storage.localStorage.includes("pt_secret_token") && !storage.sessionStorage.includes("pt_secret_token"),
    localStorageKeys: Object.keys(JSON.parse(storage.localStorage || "{}")),
    sessionStorageKeys: Object.keys(JSON.parse(storage.sessionStorage || "{}"))
  }, null, 2));

  await fs.writeFile(".omo/evidence/task-10-admin-token-network.json", JSON.stringify({
    createTokenCalls
  }, null, 2));
});
