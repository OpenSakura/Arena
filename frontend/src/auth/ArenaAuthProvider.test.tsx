// @vitest-environment jsdom

import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ArenaAuthProvider,
  finishSpaRedirect,
  handleSigninCallback,
  handleSignoutCallback,
  loadArenaPublicConfig,
  matchArenaSignoutCallback,
} from "./ArenaAuthProvider";

const useAuthMock = vi.fn();
const oidcProviderSpy = vi.fn();

vi.mock("react-oidc-context", () => ({
  AuthProvider: ({ children, ...props }: Record<string, unknown>) => {
    oidcProviderSpy(props);
    return <div data-testid="oidc-provider">{children as React.ReactNode}</div>;
  },
  useAuth: () => useAuthMock(),
}));

function Probe() {
  return <div>child-content</div>;
}

describe("ArenaAuthProvider helpers", () => {
  beforeEach(() => {
    useAuthMock.mockReset();
    oidcProviderSpy.mockReset();
    useAuthMock.mockReturnValue({
      isLoading: false,
      activeNavigator: null,
      isAuthenticated: false,
      user: null,
      error: null,
      signinRedirect: vi.fn(),
      signoutRedirect: vi.fn(),
    });
    window.sessionStorage.clear();
    window.history.replaceState({}, "", "/");
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("loads public config from the backend contract", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          anon_battle_turnstile_required: false,
          oidc: {
            issuer: "https://auth.example/application/o/arena/",
            client_id: "arena-client",
            scope: "openid profile email offline_access",
            redirect_path: "/auth/callback",
            silent_redirect_path: "/auth/silent-callback",
            post_logout_redirect_path: "/auth/logout-callback",
          },
        }),
        {
          status: 200,
          headers: { "content-type": "application/json" },
        },
      ),
    );

    const result = await loadArenaPublicConfig(new AbortController().signal);

    expect(fetchMock).toHaveBeenCalledWith("/api/v1/public-config", {
      signal: expect.any(AbortSignal),
      headers: { Accept: "application/json" },
    });
    expect(result.oidc.redirect_path).toBe("/auth/callback");
  });

  it("replaces callback urls with the saved returnTo on sign-in callback", () => {
    window.history.replaceState({}, "", "/auth/callback?code=abc&state=def");

    handleSigninCallback({ state: { returnTo: "/battle/arena-1?tab=vote#panel" } } as never);

    expect(window.location.pathname).toBe("/battle/arena-1");
    expect(window.location.search).toBe("?tab=vote");
    expect(window.location.hash).toBe("#panel");
  });

  it("falls back to home when sign-in callback state is missing or unsafe", () => {
    window.history.replaceState({}, "", "/auth/callback?code=abc&state=def");

    handleSigninCallback({ state: { returnTo: "https://evil.example/pwn" } } as never);

    expect(window.location.pathname).toBe("/");
    expect(window.location.search).toBe("");
  });

  it("routes logout callbacks back to the SPA root", () => {
    window.history.replaceState({}, "", "/auth/logout-callback?state=abc");

    handleSignoutCallback();

    expect(window.location.pathname).toBe("/");
    expect(window.location.search).toBe("");
  });

  it("matches only same-origin logout callback urls", () => {
    window.history.replaceState({}, "", "/auth/logout-callback?state=abc");

    expect(
      matchArenaSignoutCallback({
        post_logout_redirect_uri: `${window.location.origin}/auth/logout-callback`,
      }),
    ).toBe(true);
    expect(
      matchArenaSignoutCallback({
        post_logout_redirect_uri: `${window.location.origin}/auth/callback`,
      }),
    ).toBe(false);
    expect(
      matchArenaSignoutCallback({
        post_logout_redirect_uri: `https://other.example/auth/logout-callback`,
      }),
    ).toBe(false);
  });

  it("updates history and emits a popstate event when finishing SPA redirects", () => {
    const popstateListener = vi.fn();
    window.addEventListener("popstate", popstateListener);

    finishSpaRedirect("/leaderboard?filter=recent#top");

    expect(window.location.pathname).toBe("/leaderboard");
    expect(window.location.search).toBe("?filter=recent");
    expect(window.location.hash).toBe("#top");
    expect(popstateListener).toHaveBeenCalledTimes(1);

    window.removeEventListener("popstate", popstateListener);
  });
});

describe("ArenaAuthProvider component", () => {
  beforeEach(() => {
    useAuthMock.mockReset();
    oidcProviderSpy.mockReset();
    useAuthMock.mockReturnValue({
      isLoading: false,
      activeNavigator: null,
      isAuthenticated: false,
      user: null,
      error: null,
      signinRedirect: vi.fn(),
      signoutRedirect: vi.fn(),
    });
    window.sessionStorage.clear();
    window.history.replaceState({}, "", "/");
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("initializes OIDC from /api/v1/public-config with the required callback paths", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          anon_battle_turnstile_required: false,
          oidc: {
            issuer: "https://auth.example/application/o/arena/",
            client_id: "arena-client",
            scope: "openid profile email offline_access",
            redirect_path: "/auth/callback",
            silent_redirect_path: "/auth/silent-callback",
            post_logout_redirect_path: "/auth/logout-callback",
          },
        }),
        {
          status: 200,
          headers: { "content-type": "application/json" },
        },
      ),
    );

    render(
      <ArenaAuthProvider>
        <Probe />
      </ArenaAuthProvider>,
    );

    await screen.findByText("child-content");

    await waitFor(() => {
      expect(oidcProviderSpy).toHaveBeenCalledTimes(1);
    });

    const props = oidcProviderSpy.mock.calls[0]?.[0] as Record<string, unknown>;
    expect(props.authority).toBe("https://auth.example/application/o/arena");
    expect(props.client_id).toBe("arena-client");
    expect(props.response_type).toBe("code");
    expect(props.automaticSilentRenew).toBe(true);
    expect(props.redirect_uri).toBe(`${window.location.origin}/auth/callback`);
    expect(props.silent_redirect_uri).toBe(`${window.location.origin}/auth/silent-callback`);
    expect(props.post_logout_redirect_uri).toBe(`${window.location.origin}/auth/logout-callback`);
    expect(props.userStore).toBeDefined();
  });

  it("shows an error shell when public config bootstrap fails", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("boom", { status: 500 }));

    render(
      <ArenaAuthProvider>
        <Probe />
      </ArenaAuthProvider>,
    );

    await screen.findByText("Failed to load public config (500)");
    expect(oidcProviderSpy).not.toHaveBeenCalled();
  });
});
