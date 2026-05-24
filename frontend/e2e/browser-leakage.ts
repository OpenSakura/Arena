import type { Page, TestInfo } from "@playwright/test";

import { auditBrowserAuthLeakage } from "./leakage";

export {
  auditBrowserAuthLeakage,
  enforceNoBearerAuthorization,
  expectNoAuthorizationHeaders,
  expectNoForbiddenAuthText,
} from "./leakage";

const FORBIDDEN_BROWSER_SUBSTRINGS = [
  ["OIDC", "CLIENT", "SECRET"].join("_"),
  ["client", "secret"].join("_"),
  ["access", "token"].join("_"),
  ["refresh", "token"].join("_"),
  ["id", "token"].join("_"),
  ["oidc", "user"].join("."),
  ["arena", "e2e", "confidential", "client", "secret"].join("-"),
] as const;

type BrowserLeakageSnapshot = {
  localStorage: string;
  sessionStorage: string;
  visibleCookies: string;
  pageText: string;
};

export async function browserLeakageSnapshot(page: Page): Promise<BrowserLeakageSnapshot> {
  return page.evaluate(() => ({
    localStorage: JSON.stringify(Object.fromEntries(Object.entries(window.localStorage))),
    sessionStorage: JSON.stringify(Object.fromEntries(Object.entries(window.sessionStorage))),
    visibleCookies: document.cookie,
    pageText: document.body.innerText,
  }));
}

export async function expectNoBrowserCredentialLeakage(
  page: Page,
  label = "browser",
  testInfo?: TestInfo,
): Promise<BrowserLeakageSnapshot> {
  const audit = await auditBrowserAuthLeakage(page, label, testInfo);
  return {
    localStorage: audit.browser.localStorage,
    sessionStorage: audit.browser.sessionStorage,
    visibleCookies: audit.browser.cookie,
    pageText: audit.browser.pageText,
  };
}

export function forbiddenBrowserLeakagePattern(): string {
  return FORBIDDEN_BROWSER_SUBSTRINGS.map((value) => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|");
}
