import type { Page } from "@playwright/test";

import { enforceNoBearerAuthorization } from "./browser-leakage";

type MockBackendAuthConfig = {
  mode: "backend_session";
  login_path: string;
  logout_path: string;
  session_path: string;
  csrf_header_name?: string;
};

type MockBackendAuthenticatedSessionOptions = {
  csrfHeaderName?: string;
  csrfToken?: string;
  isAdmin?: boolean;
  profile?: Record<string, unknown>;
  meResponse?: Record<string, unknown>;
  sessionResponse?: Record<string, unknown>;
};

type MockBackendPublicConfigOptions = {
  csrfHeaderName?: string;
  sessionResponse?: Record<string, unknown>;
  sessionStatus?: number;
};

export const MOCK_BACKEND_AUTH: MockBackendAuthConfig = {
  mode: "backend_session",
  login_path: "/api/v1/auth/login",
  logout_path: "/api/v1/auth/logout",
  session_path: "/api/v1/auth/session",
  csrf_header_name: "X-CSRF-Token",
};

function profileDisplayName(profile: Record<string, unknown>): string {
  if (typeof profile.display_name === "string") return profile.display_name;
  if (typeof profile.name === "string") return profile.name;
  return "Playwright User";
}

function buildMockSession(options: MockBackendAuthenticatedSessionOptions = {}) {
  const profile = {
    display_name: profileDisplayName(options.profile ?? {}),
    ui_language: "en",
    zh_variant: "zh-Hans",
    jp_proficiency: null,
    translation_experience: null,
    consents: null,
    completed_at: "2026-05-24T00:00:00Z",
    ...options.profile,
  };
  const user = {
    id: "playwright-user-id",
    oidc_issuer: "https://backend-session.example/issuer",
    oidc_sub: typeof options.profile?.sub === "string" ? options.profile.sub : "playwright-user",
    created_at: "2026-05-24T00:00:00Z",
  };

  return {
    authenticated: true,
    is_admin: options.isAdmin ?? false,
    user,
    profile,
    csrf_token: options.csrfToken ?? "playwright-csrf-token",
    ...options.sessionResponse,
  };
}

async function mockPublicConfig(
  page: Page,
  csrfHeaderName = MOCK_BACKEND_AUTH.csrf_header_name,
): Promise<MockBackendAuthConfig> {
  enforceNoBearerAuthorization(page);
  const auth = { ...MOCK_BACKEND_AUTH, csrf_header_name: csrfHeaderName };

  await page.route("**/api/v1/public-config", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        anon_battle_turnstile_required: false,
        auth,
      }),
    });
  });

  return auth;
}

export async function mockSpaPublicConfig(
  page: Page,
  options: MockBackendPublicConfigOptions = {},
): Promise<MockBackendAuthConfig> {
  const auth = await mockPublicConfig(page, options.csrfHeaderName);

  await page.route("**/api/v1/auth/session", async (route) => {
    await route.fulfill({
      status: options.sessionStatus ?? 200,
      contentType: "application/json",
      body: JSON.stringify(
        options.sessionResponse ?? {
          authenticated: false,
          is_admin: false,
          user: null,
          profile: null,
          csrf_token: null,
        },
      ),
    });
  });

  return auth;
}

export async function mockSpaAuthenticatedSession(
  page: Page,
  options: MockBackendAuthenticatedSessionOptions = {},
): Promise<MockBackendAuthConfig> {
  const auth = await mockPublicConfig(page, options.csrfHeaderName);
  const session = buildMockSession(options);

  await page.context().addCookies([
    {
      name: "arena_session",
      value: "mock-backend-session-cookie",
      domain: "localhost",
      path: "/",
      httpOnly: true,
      sameSite: "Lax",
    },
  ]);

  await page.route("**/api/v1/auth/session", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(session),
    });
  });

  await page.route(/\/api\/v1\/me$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        authenticated: true,
        is_admin: session.is_admin,
        user: session.user,
        profile: session.profile,
        ...options.meResponse,
      }),
    });
  });

  return auth;
}
