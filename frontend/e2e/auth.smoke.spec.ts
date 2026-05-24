import { expect, test, type Locator, type Page } from "@playwright/test";

import {
  enforceNoBearerAuthorization,
  expectNoBrowserCredentialLeakage,
  expectNoForbiddenAuthText,
  expectNoAuthorizationHeaders,
} from "./browser-leakage";

async function submitAuthentikForm(authForm: Locator): Promise<void> {
  const action = authForm.locator("button.pf-c-button.pf-m-primary").last();
  await expect(action).toBeVisible({ timeout: 30_000 });
  await expect(action).toBeEnabled({ timeout: 30_000 });
  await action.click();
}

async function expectNoVisibleCredentialLeakage(page: Page, label: string): Promise<void> {
  expectNoForbiddenAuthText(await page.locator("body").innerText(), `${label} page text`);
  expectNoForbiddenAuthText(JSON.stringify(await page.context().storageState()), `${label} storage state`);
}

test("login and logout through backend session routes", async ({ page }, testInfo) => {
  test.setTimeout(240_000);
  const routeChain: string[] = [];
  const logoutCsrfHeaders: Array<string | undefined> = [];
  enforceNoBearerAuthorization(page);

  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname.startsWith("/api/v1/auth/")) {
      routeChain.push(`${request.method()} ${url.pathname}`);
    }
    if (request.method() === "POST" && url.pathname === "/api/v1/auth/logout") {
      logoutCsrfHeaders.push(request.headers()["x-csrf-token"]);
    }
  });

  await page.goto("/");
  await expect(page.getByRole("button", { name: "Login" })).toBeVisible();
  await expectNoVisibleCredentialLeakage(page, "before-login");
  await expectNoBrowserCredentialLeakage(page, "before-login", testInfo);

  await page.getByRole("button", { name: "Login" }).click();

  await page.waitForURL((url) => url.origin === "http://localhost:29000", { timeout: 60_000 });
  const authForm = page.getByRole("main", { name: /authentication form/i });
  const userInput = authForm.locator('input[name="uidField"]');
  await userInput.waitFor({ state: "visible", timeout: 60_000 });
  await userInput.fill("akadmin");
  const passwordInput = authForm.locator('input[name="password"]');
  await Promise.all([
    passwordInput.waitFor({ state: "visible", timeout: 60_000 }),
    submitAuthentikForm(authForm),
  ]);

  await passwordInput.fill("password1234");
  const authenticatedSessionPromise = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname === "/api/v1/auth/session" && response.request().method() === "GET";
  });
  await Promise.all([
    page.waitForURL((url) => url.origin === "http://localhost:13000" && url.pathname === "/"),
    submitAuthentikForm(authForm),
  ]);
  await expect(page.getByRole("button", { name: "Logout" })).toBeVisible({ timeout: 60_000 });

  const authenticatedSessionResponse = await authenticatedSessionPromise;
  expect(authenticatedSessionResponse.ok()).toBe(true);
  const authenticatedPayload = await authenticatedSessionResponse.json() as Record<string, unknown>;
  expect(authenticatedPayload.authenticated).toBe(true);
  expect(authenticatedPayload.user).toBeTruthy();
  expect(authenticatedPayload.csrf_token).toEqual(expect.any(String));
  expectNoForbiddenAuthText(JSON.stringify(authenticatedPayload), "authenticated session JSON");
  const logoutCsrfHeader = authenticatedPayload.csrf_token as string;

  await expectNoVisibleCredentialLeakage(page, "after-login");
  await expectNoBrowserCredentialLeakage(page, "after-login", testInfo);

  const logoutResponsePromise = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname === "/api/v1/auth/logout" && response.request().method() === "POST";
  });
  const loggedOutSessionPromise = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname === "/api/v1/auth/session" && response.request().method() === "GET";
  });
  const logoutNavigationPromise = page.waitForURL((url) => url.origin === "http://localhost:13000" && url.pathname === "/");
  await page.getByRole("button", { name: "Logout" }).click();
  const logoutResponse = await logoutResponsePromise;
  expect(logoutResponse.ok()).toBe(true);

  await logoutNavigationPromise;
  await expect(page.getByRole("button", { name: "Login" })).toBeVisible();
  const loggedOutSessionResponse = await loggedOutSessionPromise;
  expect(loggedOutSessionResponse.ok()).toBe(true);
  const loggedOutPayload = await loggedOutSessionResponse.json() as Record<string, unknown>;
  expect(loggedOutPayload.authenticated).toBe(false);
  expect(loggedOutPayload.user).toBeNull();
  expect(loggedOutPayload.csrf_token).toBeNull();

  await expectNoVisibleCredentialLeakage(page, "after-logout");
  await expectNoBrowserCredentialLeakage(page, "after-logout", testInfo);

  expect(routeChain).toContain("GET /api/v1/auth/login");
  expect(routeChain).toContain("GET /api/v1/auth/callback");
  expect(routeChain).toContain("POST /api/v1/auth/logout");
  expect(routeChain.filter((entry) => entry === "GET /api/v1/auth/session").length).toBeGreaterThanOrEqual(2);
  expect(logoutCsrfHeaders).toHaveLength(1);
  expect(logoutCsrfHeaders[0]).toBe(logoutCsrfHeader);
  expect(logoutCsrfHeaders[0]).not.toHaveLength(0);
  expectNoAuthorizationHeaders(page);
});
