// @vitest-environment jsdom

import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useArenaAuth } from "@/hooks/useArenaAuth";
import { createTestI18n, TestI18nProvider } from "@/i18n/test-utils";

import {
  ArenaAuthProvider,
  arenaAuthNavigation,
  loadArenaPublicConfig,
} from "./ArenaAuthProvider";
import { normalizeReturnTo } from "./session";

const PUBLIC_CONFIG = {
  anon_battle_turnstile_required: false,
  auth: {
    mode: "backend_session",
    login_path: "/api/v1/auth/login",
    logout_path: "/api/v1/auth/logout",
    session_path: "/api/v1/auth/session",
  },
} as const;

const AUTHENTICATED_SESSION = {
  authenticated: true,
  is_admin: true,
  user: {
    id: "user-1",
    oidc_issuer: "https://issuer.example",
    oidc_sub: "subject-1",
    created_at: "2026-05-24T00:00:00Z",
  },
  profile: {
    display_name: "Arena User",
    ui_language: "en",
    zh_variant: "simplified",
    jp_proficiency: null,
    translation_experience: null,
    consents: null,
    completed_at: "2026-05-24T00:00:00Z",
  },
  csrf_token: "csrf-token-1",
} as const;

function jsonResponse(body: unknown, init: ResponseInit = {}) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
    ...init,
  });
}

function createNavigateSpy() {
  return vi.spyOn(arenaAuthNavigation, "to").mockImplementation(() => undefined);
}

function Probe() {
  const auth = useArenaAuth();

  return (
    <div>
      <div>child-content</div>
      <div data-testid="auth-status">{auth.authStatus}</div>
      <div data-testid="is-authenticated">{String(auth.isAuthenticated)}</div>
      <div data-testid="is-loading">{String(auth.isLoading)}</div>
      <div data-testid="csrf-token">{auth.csrfToken ?? "none"}</div>
      <div data-testid="session-error">{auth.sessionError ?? "none"}</div>
      <div data-testid="user-id">{auth.user?.id ?? "none"}</div>
      <div data-testid="user-label">{auth.user?.profile.display_name ?? "none"}</div>
      <div data-testid="has-access-token">{String("accessToken" in auth)}</div>
      <div data-testid="has-refresh-token">{String("refreshToken" in auth)}</div>
      <div data-testid="has-id-token">{String("idToken" in auth)}</div>
      <div data-testid="has-client-secret">{String("clientSecret" in auth)}</div>
      <div data-testid="has-auth-headers">{String("headers" in auth)}</div>
      <button type="button" onClick={() => void auth.signinRedirect({ state: { returnTo: "/battle/1?tab=vote#panel" } })}>
        Login
      </button>
      <button type="button" onClick={() => void auth.signinRedirect({ state: { returnTo: "https://evil.example/pwn" } })}>
        Unsafe Login
      </button>
      <button type="button" onClick={() => void auth.signoutRedirect()}>
        Logout
      </button>
    </div>
  );
}

async function renderAuthProvider(fetchMock: ReturnType<typeof vi.fn>, locale: "en" | "zh" = "en") {
  vi.spyOn(globalThis, "fetch").mockImplementation(fetchMock);
  const i18n = await createTestI18n(locale);

  return render(
    <TestI18nProvider i18n={i18n}>
      <ArenaAuthProvider>
        <Probe />
      </ArenaAuthProvider>
    </TestI18nProvider>,
  );
}

async function renderProviderWithFetch(fetchMock: ReturnType<typeof vi.fn>, locale: "en" | "zh" = "en") {
  await renderAuthProvider(fetchMock, locale);

  await screen.findByText("child-content");
}

describe("ArenaAuthProvider helpers", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    window.history.replaceState({}, "", "/");
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("loads public config from the backend-session contract", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(PUBLIC_CONFIG));

    const result = await loadArenaPublicConfig(new AbortController().signal);

    expect(fetchMock).toHaveBeenCalledWith("/api/v1/public-config", {
      signal: expect.any(AbortSignal),
      headers: { Accept: "application/json" },
    });
    expect(result.auth).toEqual(PUBLIC_CONFIG.auth);
  });

  it("normalizes login returnTo values to backend-safe paths", () => {
    expect(normalizeReturnTo(`${window.location.origin}/battle/1?tab=vote#panel`)).toBe(
      "/battle/1?tab=vote#panel",
    );
    expect(normalizeReturnTo("https://evil.example/pwn")).toBe("/");
  });
});

describe("ArenaAuthProvider component", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    window.history.replaceState({}, "", "/");
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("localizes loading authentication copy while bootstrap is pending", async () => {
    const fetchMock = vi.fn(() => new Promise<Response>(() => undefined));

    await renderAuthProvider(fetchMock, "zh");

    expect(screen.getByText("正在加载登录信息…")).toBeDefined();
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/public-config", {
      signal: expect.any(AbortSignal),
      headers: { Accept: "application/json" },
    });
  });

  it("bootstraps authenticated session state from backend session JSON", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(PUBLIC_CONFIG))
      .mockResolvedValueOnce(jsonResponse(AUTHENTICATED_SESSION));

    await renderProviderWithFetch(fetchMock);

    expect(fetchMock).toHaveBeenNthCalledWith(1, "/api/v1/public-config", {
      signal: expect.any(AbortSignal),
      headers: { Accept: "application/json" },
    });
    expect(fetchMock).toHaveBeenNthCalledWith(2, "/api/v1/auth/session", {
      signal: expect.any(AbortSignal),
      credentials: "include",
      headers: { Accept: "application/json" },
    });
    expect(screen.getByTestId("auth-status").textContent).toBe("authenticated");
    expect(screen.getByTestId("is-authenticated").textContent).toBe("true");
    expect(screen.getByTestId("is-loading").textContent).toBe("false");
    expect(screen.getByTestId("csrf-token").textContent).toBe("csrf-token-1");
    expect(screen.getByTestId("session-error").textContent).toBe("none");
    expect(screen.getByTestId("user-id").textContent).toBe("user-1");
    expect(screen.getByTestId("user-label").textContent).toBe("Arena User");
    expect(screen.getByTestId("has-access-token").textContent).toBe("false");
    expect(screen.getByTestId("has-refresh-token").textContent).toBe("false");
    expect(screen.getByTestId("has-id-token").textContent).toBe("false");
    expect(screen.getByTestId("has-client-secret").textContent).toBe("false");
    expect(screen.getByTestId("has-auth-headers").textContent).toBe("false");
  });

  it("bootstraps unauthenticated backend session state without throwing", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(PUBLIC_CONFIG))
      .mockResolvedValueOnce(
        jsonResponse({
          authenticated: false,
          is_admin: false,
          user: null,
          profile: null,
          csrf_token: null,
        }),
      );

    await renderProviderWithFetch(fetchMock);

    expect(screen.getByTestId("auth-status").textContent).toBe("unauthenticated");
    expect(screen.getByTestId("is-authenticated").textContent).toBe("false");
    expect(screen.getByTestId("csrf-token").textContent).toBe("none");
    expect(screen.getByTestId("user-id").textContent).toBe("none");
  });

  it("navigates login compatibility calls to backend login with sanitized returnTo", async () => {
    const navigateSpy = createNavigateSpy();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(PUBLIC_CONFIG))
      .mockResolvedValueOnce(jsonResponse({ authenticated: false, is_admin: false, user: null, profile: null, csrf_token: null }));

    await renderProviderWithFetch(fetchMock);

    screen.getByRole("button", { name: "Login" }).click();
    screen.getByRole("button", { name: "Unsafe Login" }).click();

    expect(navigateSpy).toHaveBeenNthCalledWith(1, "/api/v1/auth/login?returnTo=%2Fbattle%2F1%3Ftab%3Dvote%23panel");
    expect(navigateSpy).toHaveBeenNthCalledWith(2, "/api/v1/auth/login?returnTo=%2F");
  });

  it("posts logout with credentials and CSRF, clears local auth state, and navigates home", async () => {
    const navigateSpy = createNavigateSpy();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(PUBLIC_CONFIG))
      .mockResolvedValueOnce(jsonResponse(AUTHENTICATED_SESSION))
      .mockResolvedValueOnce(jsonResponse({ ok: true, authenticated: false, logout_url: null }));

    await renderProviderWithFetch(fetchMock);

    screen.getByRole("button", { name: "Logout" }).click();

    await waitFor(() => {
      expect(fetchMock).toHaveBeenNthCalledWith(3, "/api/v1/auth/logout", {
        method: "POST",
        credentials: "include",
        headers: {
          Accept: "application/json",
          "X-CSRF-Token": "csrf-token-1",
        },
      });
    });
    await waitFor(() => {
      expect(screen.getByTestId("auth-status").textContent).toBe("unauthenticated");
    });
    expect(screen.getByTestId("csrf-token").textContent).toBe("none");
    expect(navigateSpy).toHaveBeenCalledWith("/");
  });

  it("shows an error shell when public config bootstrap fails", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("boom", { status: 500 }));

    await renderAuthProvider(fetchMock);

    await screen.findByText("Failed to load public config (500)");
    expect(screen.getByText("Please refresh the page to try again.")).toBeDefined();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("localizes session error shell when backend session bootstrap fails", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(PUBLIC_CONFIG))
      .mockResolvedValueOnce(new Response("unauthorized", { status: 401 }));

    await renderAuthProvider(fetchMock, "zh");

    await screen.findByText("加载登录会话失败（401）");
    expect(screen.getByText("请刷新页面后重试。")).toBeDefined();
    expect(fetchMock).toHaveBeenNthCalledWith(1, "/api/v1/public-config", {
      signal: expect.any(AbortSignal),
      headers: { Accept: "application/json" },
    });
    expect(fetchMock).toHaveBeenNthCalledWith(2, "/api/v1/auth/session", {
      signal: expect.any(AbortSignal),
      credentials: "include",
      headers: { Accept: "application/json" },
    });
  });

  it("does not call provider token endpoints or write oidc.user sessionStorage keys", async () => {
    const setItemSpy = vi.spyOn(window.sessionStorage.__proto__, "setItem");
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(PUBLIC_CONFIG))
      .mockResolvedValueOnce(jsonResponse(AUTHENTICATED_SESSION));

    await renderProviderWithFetch(fetchMock);

    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "/api/v1/public-config",
      "/api/v1/auth/session",
    ]);
    expect(fetchMock.mock.calls.some((call) => String(call[0]).includes("/token"))).toBe(false);
    expect(fetchMock.mock.calls.some((call) => String(call[0]).includes("/authorize"))).toBe(false);
    expect(setItemSpy.mock.calls.some(([key]) => String(key).startsWith("oidc.user:"))).toBe(false);
    expect(Object.keys(window.sessionStorage).some((key) => key.startsWith("oidc.user:"))).toBe(false);
    expect(JSON.stringify([...setItemSpy.mock.calls])).not.toMatch(/accessToken|refreshToken|idToken|clientSecret/);
  });
});
