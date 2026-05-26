import { expect, test, type Page } from "@playwright/test";

import { auditBrowserAuthLeakage, expectNoAuthorizationHeaders } from "./browser-leakage";
import { mockSpaAuthenticatedSession, mockSpaPublicConfig } from "./spa-auth";

const USER_LOCALE_STORAGE_KEY = "user-locale";

type ProfileUpdateCall = {
  csrfHeader: string | undefined;
  method: string;
  pathname: string;
  payload: Record<string, unknown>;
};

async function seedUserLocale(page: Page, locale: string): Promise<void> {
  await page.addInitScript(
    ({ key, value }: { key: string; value: string }) => {
      window.localStorage.setItem(key, value);
    },
    { key: USER_LOCALE_STORAGE_KEY, value: locale },
  );
}

async function expectHtmlLang(page: Page, expected: string): Promise<void> {
  await expect.poll(() => page.evaluate(() => document.documentElement.lang)).toBe(expected);
}

async function expectUserLocale(page: Page, expected: string | null): Promise<void> {
  await expect
    .poll(() => page.evaluate((key) => window.localStorage.getItem(key), USER_LOCALE_STORAGE_KEY))
    .toBe(expected);
}

async function expectPathWithoutLocalePrefix(page: Page, expectedPathname: string): Promise<void> {
  await expect.poll(() => new URL(page.url()).pathname).toBe(expectedPathname);
  const pathname = new URL(page.url()).pathname;
  expect(pathname).not.toMatch(/^\/(?:en|zh)(?:\/|$)/);
}

async function expectEnglishHome(page: Page): Promise<void> {
  await expect(page.getByText("Open-source translation arena")).toBeVisible();
  await expect(page.getByText("Pairwise, blind comparisons of JP>ZH", { exact: false })).toBeVisible();
  await expect(page).toHaveTitle("Home | OpenSakura Arena");
}

async function expectChineseHome(page: Page): Promise<void> {
  await expect(page.getByText("开源翻译对战平台")).toBeVisible();
  await expect(page.getByText("对日文到中文的轻小说风格翻译", { exact: false })).toBeVisible();
  await expect(page).toHaveTitle("首页 | OpenSakura Arena");
}

test("persists Chinese switch across reload without URL locale prefixes", async ({ page }) => {
  await mockSpaPublicConfig(page);

  await page.goto("/");

  await expectPathWithoutLocalePrefix(page, "/");
  await expectHtmlLang(page, "en");
  await expectUserLocale(page, null);
  await expectEnglishHome(page);
  await expect(page.getByRole("button", { name: "Start a Battle" })).toBeVisible();
  await expect(page.getByRole("link", { name: "View Leaderboard" })).toBeVisible();

  const languageSwitcher = page.getByRole("combobox", { name: "Language / 语言" });
  await expect(languageSwitcher).toHaveValue("en");
  await languageSwitcher.selectOption("zh");

  await expect(languageSwitcher).toHaveValue("zh");
  await expectChineseHome(page);
  await expectHtmlLang(page, "zh");
  await expectUserLocale(page, "zh");
  await expectPathWithoutLocalePrefix(page, "/");
  await expect(page.getByRole("button", { name: "开始对战" })).toBeVisible();
  await expect(page.getByRole("link", { name: "查看排行榜" })).toBeVisible();

  await page.reload();

  await expectChineseHome(page);
  await expectHtmlLang(page, "zh");
  await expectUserLocale(page, "zh");
  await expectPathWithoutLocalePrefix(page, "/");
});

test("falls back to English for unsupported persisted locale", async ({ page }) => {
  await seedUserLocale(page, "fr-FR");
  await mockSpaPublicConfig(page);

  await page.goto("/");

  await expectPathWithoutLocalePrefix(page, "/");
  await expectHtmlLang(page, "en");
  await expectUserLocale(page, "fr-FR");
  await expectEnglishHome(page);
  await expect(page.getByRole("button", { name: "Start a Battle" })).toBeVisible();
  await expect(page.getByRole("combobox", { name: "Language / 语言" })).toHaveValue("en");
});

test("localized auth error route keeps unprefixed URL", async ({ page }) => {
  await seedUserLocale(page, "zh");
  await mockSpaPublicConfig(page);

  await page.goto("/auth/error");

  await expectPathWithoutLocalePrefix(page, "/auth/error");
  await expectHtmlLang(page, "zh");
  await expectUserLocale(page, "zh");
  await expect(page).toHaveTitle("认证错误 | OpenSakura Arena");
  await expect(page.getByRole("heading", { name: "认证错误" })).toBeVisible();
  await expect(page.getByText("认证无法完成，请重试。", { exact: true })).toBeVisible();
});

test("authenticated profile ui_language initializes Chinese when storage is absent", async ({ page }, testInfo) => {
  await mockSpaAuthenticatedSession(page, {
    profile: {
      sub: "i18n-profile-zh-user",
      display_name: "I18n Profile ZH",
      ui_language: "zh",
      zh_variant: "zh-Hans",
    },
  });

  await page.goto("/");

  await expectPathWithoutLocalePrefix(page, "/");
  await expectChineseHome(page);
  await expectHtmlLang(page, "zh");
  await expectUserLocale(page, "zh");
  await expect(page.getByRole("link", { name: "开始对战" })).toBeVisible();
  expectNoAuthorizationHeaders(page);
  await auditBrowserAuthLeakage(page, "i18n-profile-zh-flow", testInfo);
});

test("failed profile update does not revert selected language", async ({ page }, testInfo) => {
  const profileUpdateCalls: ProfileUpdateCall[] = [];

  await mockSpaAuthenticatedSession(page, {
    profile: {
      sub: "i18n-profile-update-failure-user",
      display_name: "I18n Update Failure",
      ui_language: "en",
      zh_variant: "zh-Hans",
      jp_proficiency: null,
      translation_experience: null,
      consents: null,
    },
  });

  await page.route(/\/api\/v1\/me\/profile$/, async (route) => {
    const request = route.request();
    if (request.method() !== "PUT") {
      await route.abort();
      return;
    }

    profileUpdateCalls.push({
      csrfHeader: request.headers()["x-csrf-token"],
      method: request.method(),
      pathname: new URL(request.url()).pathname,
      payload: request.postDataJSON() as Record<string, unknown>,
    });

    await route.fulfill({
      status: 500,
      contentType: "application/json",
      body: JSON.stringify({ detail: "Profile update failed" }),
    });
  });

  await page.goto("/");

  await expectPathWithoutLocalePrefix(page, "/");
  await expectHtmlLang(page, "en");
  await expectUserLocale(page, "en");
  await expectEnglishHome(page);

  await page.getByRole("combobox", { name: "Language / 语言" }).selectOption("zh");

  await expectChineseHome(page);
  await expectHtmlLang(page, "zh");
  await expectUserLocale(page, "zh");
  await expectPathWithoutLocalePrefix(page, "/");
  await expect(page.getByRole("link", { name: "开始对战" })).toBeVisible();
  await expect(page.getByText("Failed to save profile")).toHaveCount(0);
  await expect(page.getByText("保存资料失败")).toHaveCount(0);
  await expect.poll(() => profileUpdateCalls.length).toBe(1);

  expect(profileUpdateCalls[0]).toMatchObject({
    csrfHeader: "playwright-csrf-token",
    method: "PUT",
    pathname: "/api/v1/me/profile",
    payload: {
      display_name: "I18n Update Failure",
      ui_language: "zh",
      zh_variant: "zh-Hans",
      jp_proficiency: null,
      translation_experience: null,
      consents: null,
    },
  });
  expectNoAuthorizationHeaders(page);
  await auditBrowserAuthLeakage(page, "i18n-profile-update-failure-flow", testInfo);
});

test.describe("browser locale zh-CN with authenticated profile", () => {
  test.use({ locale: "zh-CN" });

  test("profile ui_language en wins and persists English", async ({ page }, testInfo) => {
    await mockSpaAuthenticatedSession(page, {
      profile: {
        sub: "i18n-profile-en-user",
        display_name: "I18n Profile EN",
        ui_language: "en",
        zh_variant: "zh-Hans",
      },
    });

    await page.goto("/");

    await expect.poll(() => page.evaluate(() => navigator.language)).toBe("zh-CN");
    await expectPathWithoutLocalePrefix(page, "/");
    await expectHtmlLang(page, "en");
    await expectUserLocale(page, "en");
    await expectEnglishHome(page);
    await expect(page.getByRole("link", { name: "Start a Battle" })).toBeVisible();
    expectNoAuthorizationHeaders(page);
    await auditBrowserAuthLeakage(page, "i18n-profile-en-browser-zh-flow", testInfo);
  });
});
