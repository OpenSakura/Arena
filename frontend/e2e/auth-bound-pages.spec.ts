import { expect, test } from "@playwright/test";

import { mockSpaAuthenticatedSession, mockSpaPublicConfig } from "./spa-auth";

test("onboarding shows anonymous guard", async ({ page }) => {
  await mockSpaPublicConfig(page, { scope: "openid profile email" });

  await page.route("**/api/v1/me", async (route) => {
    await route.fulfill({
      status: 401,
      contentType: "application/json",
      body: JSON.stringify({ authenticated: false }),
    });
  });

  await page.goto("/onboarding");

  await expect(page.getByText("Login required to save")).toBeVisible();
  await expect(page.getByRole("button", { name: "Save profile" })).toBeDisabled();
});

test("onboarding shows explicit re-login messaging for expired sessions", async ({ page }) => {
  await mockSpaPublicConfig(page, { scope: "openid profile email" });

  await page.route("**/api/v1/me", async (route) => {
    await route.fulfill({
      status: 401,
      contentType: "application/json",
      body: JSON.stringify({
        authenticated: false,
        error: "RefreshTokenExpired",
      }),
    });
  });

  await page.addInitScript(() => {
    sessionStorage.setItem("oidc.user:http://localhost:13000/mock-oidc:arena", JSON.stringify({
      expires_at: Math.floor(Date.now() / 1000) - 3600, // Expired!
      token_type: "Bearer",
      scope: "openid profile email",
      profile: { sub: "user123" }
    }));
  });

  await page.goto("/onboarding");
  
  await expect(page.getByText("Session expired", { exact: true })).toBeVisible();
  await expect(
    page.getByText(/Your session expired before we could load or save your profile/i),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Save profile" })).toBeDisabled();
});

test("onboarding saves profile payload for authenticated users", async ({ page }) => {
  const saveCalls: Array<{ authHeader: string | undefined; payload: Record<string, unknown> }> = [];

  await mockSpaAuthenticatedSession(page, {
    accessToken: "frontend-e2e-access-token",
    oidc: { scope: "openid profile email" },
    meResponse: {
      profile: {
        display_name: "Existing User",
        ui_language: "en",
        zh_variant: "zh-Hans",
        jp_proficiency: { jlpt: "N2" },
        translation_experience: { jp_zh: { years: "1-3", roles: ["translator"] } },
        consents: { research_use: false },
        completed_at: "2026-02-19T00:00:00.000Z",
      },
    },
    profile: { sub: "user123" },
  });

  await page.route(/\/api\/v1\/me\/profile$/, async (route) => {
    const payload = route.request().postDataJSON() as Record<string, unknown>;
    saveCalls.push({
      authHeader: route.request().headers()["authorization"],
      payload,
    });

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        authenticated: true,
        is_admin: false,
        profile: {
          ...payload,
          completed_at: "2026-02-19T12:34:56.000Z",
        },
      }),
    });
  });

  await page.goto("/onboarding");

  await expect(page.locator("#display-name")).toHaveValue("Existing User");

  await page.getByLabel("Display name (optional)").fill("  Playwright Profile User  ");
  await page.selectOption("#ui-language", "zh");
  await page.selectOption("#zh-variant", "zh-Hant");
  await page.selectOption("#jlpt", "N1");
  await page.selectOption("#experience-years", "5+");
  await page.getByRole("button", { name: "editor" }).click();
  await page.getByRole("checkbox").check();

  await page.getByRole("button", { name: "Save profile" }).click();

  await expect(page.getByText(/Saved/)).toBeVisible();

  expect(saveCalls).toHaveLength(1);
  expect(saveCalls[0]?.authHeader).toBe("Bearer frontend-e2e-access-token");
  expect(saveCalls[0]?.payload).toMatchObject({
    display_name: "Playwright Profile User",
    ui_language: "zh",
    zh_variant: "zh-Hant",
    jp_proficiency: { jlpt: "N1" },
    consents: { research_use: true },
  });
});

test("admin routes redirect unauthenticated users to home page", async ({ page }) => {
  await mockSpaPublicConfig(page, { scope: "openid profile email" });

  await page.route("**/api/v1/me", async (route) => {
    await route.fulfill({
      status: 401,
      contentType: "application/json",
      body: JSON.stringify({ authenticated: false }),
    });
  });

  await page.goto("/admin/models");

  await page.waitForURL((url) => url.pathname === "/");
  const url = new URL(page.url());
  expect(url.pathname).toBe("/");
  expect(url.searchParams.has("callbackUrl")).toBe(true);
  expect(url.searchParams.get("callbackUrl")).toBe("/admin/models");
});

test("admin models page performs a basic authenticated create flow", async ({ page }) => {
  const createCalls: Array<{ authHeader: string | undefined; payload: Record<string, unknown> }> = [];

  await mockSpaAuthenticatedSession(page, {
    accessToken: "frontend-admin-access-token",
    isAdmin: true,
    oidc: { scope: "openid profile email" },
    profile: {
      sub: "admin123",
      name: "Playwright Admin",
      email: "admin@example.com",
    },
  });

  await page.route("**/api/v1/admin/models", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ models: [] }),
      });
      return;
    }

    if (route.request().method() === "POST") {
      const payload = route.request().postDataJSON() as Record<string, unknown>;
      createCalls.push({
        authHeader: route.request().headers()["authorization"],
        payload,
      });

      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          id: "model-playwright-created",
          display_name: payload.display_name,
          provider_type: payload.provider_type,
          model_name: payload.model_name,
          base_url: payload.base_url,
          enabled: payload.enabled,
          visibility: payload.visibility,
          tags: null,
          temperature: null,
          frequency_penalty: null,
          presence_penalty: null,
          system_prompt: null,
          user_prompt: null,
          params: null,
          has_api_key: false,
          created_at: "2026-02-19T00:00:00.000Z",
          updated_at: "2026-02-19T00:00:00.000Z",
        }),
      });
      return;
    }

    await route.abort();
  });

  await page.goto("/admin/models");

  await expect(page.getByText("Model Registry")).toBeVisible();
  await page.getByLabel("Display name").fill("Playwright Created Model");
  await page.getByLabel("Model name").fill("playwright-model");
  await page.getByLabel("Base URL").fill("http://127.0.0.1:18080");

  await page.getByRole("button", { name: "Create" }).click();

  await expect(page.getByText("Playwright Created Model")).toBeVisible();

  expect(createCalls).toHaveLength(1);
  expect(createCalls[0]?.authHeader).toBe("Bearer frontend-admin-access-token");
  expect(createCalls[0]?.payload).toMatchObject({
    display_name: "Playwright Created Model",
    provider_type: "openai_compat",
    model_name: "playwright-model",
    base_url: "http://127.0.0.1:18080",
    enabled: true,
    visibility: "public",
  });
});
