import { expect, test } from "@playwright/test";
import { encode } from "next-auth/jwt";

test("onboarding shows anonymous guard", async ({ page }) => {
  await page.route("**/api/auth/session*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: "null",
    });
  });

  await page.goto("/onboarding");

  await expect(page.getByText("Login required to save")).toBeVisible();
  await expect(page.getByRole("button", { name: "Save profile" })).toBeDisabled();
});

test("onboarding saves profile payload for authenticated users", async ({ page }) => {
  const saveCalls: Array<{ authHeader: string | undefined; payload: Record<string, unknown> }> = [];

  await page.route("**/api/auth/session*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        user: { name: "Arena E2E", email: "arena-e2e@example.com" },
        expires: "2099-01-01T00:00:00.000Z",
        accessToken: "frontend-e2e-access-token",
      }),
    });
  });

  await page.route(/\/api\/v1\/me$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        authenticated: true,
        is_admin: false,
        profile: {
          display_name: "Existing User",
          ui_language: "en",
          zh_variant: "zh-Hans",
          jp_proficiency: { jlpt: "N2" },
          translation_experience: { jp_zh: { years: "1-3", roles: ["translator"] } },
          consents: { research_use: false },
          completed_at: "2026-02-19T00:00:00.000Z",
        },
      }),
    });
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
  await page.goto("/admin/models");

  await page.waitForURL((url) => url.pathname === "/");
  const url = new URL(page.url());
  expect(url.pathname).toBe("/");
  expect(url.searchParams.has("callbackUrl")).toBe(true);
});

test("admin models page performs a basic authenticated create flow", async ({ page }) => {
  const createCalls: Array<{ authHeader: string | undefined; payload: Record<string, unknown> }> = [];

  const sessionToken = await encode({
    token: { name: "Admin Arena", email: "admin@example.com" },
    secret: "arena-frontend-e2e-nextauth-secret",
  });

  await page.context().addCookies([
    {
      name: "next-auth.session-token",
      value: sessionToken,
      domain: "localhost",
      path: "/",
    },
  ]);

  await page.route("**/api/auth/session*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        user: { name: "Admin Arena", email: "admin@example.com" },
        expires: "2099-01-01T00:00:00.000Z",
        accessToken: "frontend-admin-access-token",
      }),
    });
  });

  await page.route(/\/api\/v1\/me$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        authenticated: true,
        is_admin: true,
      }),
    });
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
          extra_body: null,
          default_params: null,
          prompt_template_id: null,
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
