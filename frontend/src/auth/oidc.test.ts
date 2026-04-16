// @vitest-environment jsdom

import { describe, expect, it } from "vitest";

import {
  buildAbsoluteUrl,
  buildOidcSettings,
  createSessionStorageStore,
  deriveSessionError,
  extractReturnTo,
  normalizeReturnTo,
  type PublicOidcConfig,
} from "./oidc";

const SAMPLE_OIDC_CONFIG: PublicOidcConfig = {
  issuer: "https://auth.example/application/o/arena/",
  client_id: "arena-client",
  scope: "openid profile email offline_access",
  redirect_path: "/auth/callback",
  silent_redirect_path: "/auth/silent-callback",
  post_logout_redirect_path: "/auth/logout-callback",
};

describe("oidc helpers", () => {
  it("builds absolute callback urls from the SPA origin", () => {
    expect(buildAbsoluteUrl("https://arena.example", "/auth/callback")).toBe(
      "https://arena.example/auth/callback",
    );
  });

  it("normalizes returnTo values to same-origin paths only", () => {
    window.history.replaceState({}, "", "/current");

    expect(normalizeReturnTo("/battle/123?tab=votes#results")).toBe(
      "/battle/123?tab=votes#results",
    );
    expect(normalizeReturnTo(`${window.location.origin}/onboarding`)).toBe("/onboarding");
    expect(normalizeReturnTo("https://malicious.example/phish")).toBe("/");
    expect(normalizeReturnTo("")).toBe("/");
  });

  it("extracts returnTo from OIDC state payloads", () => {
    expect(extractReturnTo({ returnTo: "/leaderboard?mode=recent" })).toBe(
      "/leaderboard?mode=recent",
    );
    expect(extractReturnTo("/battle/new")).toBe("/battle/new");
    expect(extractReturnTo({ other: true })).toBe("/");
  });

  it("builds code + PKCE settings with sessionStorage-backed user store", async () => {
    const settings = buildOidcSettings(SAMPLE_OIDC_CONFIG, "https://arena.example");

    expect(settings.authority).toBe("https://auth.example/application/o/arena");
    expect(settings.client_id).toBe("arena-client");
    expect(settings.scope).toBe("openid profile email offline_access");
    expect(settings.response_type).toBe("code");
    expect(settings.redirect_uri).toBe("https://arena.example/auth/callback");
    expect(settings.silent_redirect_uri).toBe("https://arena.example/auth/silent-callback");
    expect(settings.post_logout_redirect_uri).toBe("https://arena.example/auth/logout-callback");
    expect(settings.automaticSilentRenew).toBe(true);
    expect(settings.disablePKCE).toBe(false);

    const userStore = settings.userStore;
    expect(userStore).toBeDefined();

    await userStore?.set("oidc.user:test", "stored-user");
    expect(await userStore?.get("oidc.user:test")).toBe("stored-user");
    expect(window.sessionStorage.length).toBeGreaterThan(0);
  });

  it("creates a reusable sessionStorage store helper", async () => {
    const store = createSessionStorageStore();

    await store.set("arena.auth:key", "value");

    expect(await store.get("arena.auth:key")).toBe("value");
    expect(window.sessionStorage.length).toBeGreaterThan(0);
  });

  it("maps refresh failures onto stable session error codes", () => {
    expect(deriveSessionError(null, null, null)).toBeNull();
    expect(deriveSessionError(null, { profile: {} } as never, null)).toBe("RefreshTokenExpired");
    expect(
      deriveSessionError(new Error("metadata discovery failed"), { profile: {} } as never, "token"),
    ).toBe("RefreshDiscoveryFailed");
    expect(
      deriveSessionError(new Error("no refresh token available"), { profile: {} } as never, "token"),
    ).toBe("RefreshTokenMissing");
    expect(
      deriveSessionError(new Error("invalid_grant"), { profile: {} } as never, "token"),
    ).toBe("RefreshTokenExpired");
    expect(
      deriveSessionError(new Error("network exploded"), { profile: {} } as never, "token"),
    ).toBe("RefreshTokenError");
  });
});
