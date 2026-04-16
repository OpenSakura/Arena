import type { Page } from "@playwright/test";

export type MockSpaOidcConfig = {
  issuer: string;
  client_id: string;
  scope: string;
  redirect_path: string;
  silent_redirect_path: string;
  post_logout_redirect_path: string;
};

export const MOCK_SPA_OIDC: MockSpaOidcConfig = {
  issuer: "http://localhost:13000/mock-oidc",
  client_id: "arena",
  scope: "openid profile email offline_access",
  redirect_path: "/auth/callback",
  silent_redirect_path: "/auth/silent-callback",
  post_logout_redirect_path: "/auth/logout-callback",
};

type MockSpaAuthenticatedSessionOptions = {
  accessToken?: string;
  expiresAt?: number;
  isAdmin?: boolean;
  oidc?: Partial<MockSpaOidcConfig>;
  profile?: Record<string, unknown>;
  meResponse?: Record<string, unknown>;
  refreshToken?: string;
};

export function buildMockOidcStorageKey(
  issuer = MOCK_SPA_OIDC.issuer,
  clientId = MOCK_SPA_OIDC.client_id,
): string {
  return `oidc.user:${issuer}:${clientId}`;
}

export async function mockSpaPublicConfig(
  page: Page,
  oidcOverrides: Partial<MockSpaOidcConfig> = {},
): Promise<MockSpaOidcConfig> {
  const oidc = { ...MOCK_SPA_OIDC, ...oidcOverrides };

  await page.route("**/api/v1/public-config", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        anon_battle_turnstile_required: false,
        oidc,
      }),
    });
  });

  return oidc;
}

export async function mockSpaAuthenticatedSession(
  page: Page,
  options: MockSpaAuthenticatedSessionOptions = {},
): Promise<MockSpaOidcConfig> {
  const oidc = await mockSpaPublicConfig(page, options.oidc);
  const accessToken = options.accessToken ?? "frontend-e2e-access-token";
  const expiresAt = options.expiresAt ?? Math.floor(Date.now() / 1000) + 3600;
  const storageKey = buildMockOidcStorageKey(oidc.issuer, oidc.client_id);
  const user = {
    access_token: accessToken,
    expires_at: expiresAt,
    token_type: "Bearer",
    scope: oidc.scope,
    profile: {
      sub: "playwright-user",
      name: "Playwright User",
      email: "playwright@example.com",
      ...options.profile,
    },
    ...(options.refreshToken ? { refresh_token: options.refreshToken } : {}),
  };

  await page.route(/\/api\/v1\/me$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        authenticated: true,
        is_admin: options.isAdmin ?? false,
        ...options.meResponse,
      }),
    });
  });

  await page.addInitScript(
    ({ key, value }) => {
      window.sessionStorage.setItem(key, value);
    },
    { key: storageKey, value: JSON.stringify(user) },
  );

  return oidc;
}
