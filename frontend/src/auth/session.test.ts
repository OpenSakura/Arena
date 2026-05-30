// @vitest-environment jsdom

import { describe, expect, it } from "vitest";

import {
  assertBackendSessionConfig,
  extractReturnTo,
  normalizeReturnTo,
  toBackendSessionUser,
  type PublicConfig,
} from "./session";

const BACKEND_SESSION_CONFIG: PublicConfig = {
  anon_battle_turnstile_required: false,
  auth: {
    mode: "backend_session",
    login_path: "/api/v1/auth/login",
    logout_path: "/api/v1/auth/logout",
    session_path: "/api/v1/auth/session",
    csrf_header_name: "X-CSRF-Token",
  },
};

describe("session auth helpers", () => {
  it("normalizes returnTo values to same-origin paths only", () => {
    window.history.replaceState({}, "", "/current");

    expect(normalizeReturnTo("/battle/123?tab=votes#results")).toBe(
      "/battle/123?tab=votes#results",
    );
    expect(normalizeReturnTo(`${window.location.origin}/onboarding`)).toBe("/onboarding");
    expect(normalizeReturnTo("https://malicious.example/phish")).toBe("/");
    expect(normalizeReturnTo("")).toBe("/");
  });

  it("extracts returnTo from redirect compatibility state payloads", () => {
    expect(extractReturnTo({ returnTo: "/leaderboard?mode=recent" })).toBe(
      "/leaderboard?mode=recent",
    );
    expect(extractReturnTo("/battle/new")).toBe("/battle/new");
    expect(extractReturnTo({ other: true })).toBe("/");
  });

  it("asserts backend-session public config paths", () => {
    expect(assertBackendSessionConfig(BACKEND_SESSION_CONFIG)).toEqual(BACKEND_SESSION_CONFIG.auth);
    expect(() =>
      assertBackendSessionConfig({
        ...BACKEND_SESSION_CONFIG,
        auth: { ...BACKEND_SESSION_CONFIG.auth, mode: "unsupported" as "backend_session" },
      }),
    ).toThrow("Unsupported authentication mode");
    expect(() =>
      assertBackendSessionConfig({
        ...BACKEND_SESSION_CONFIG,
        auth: { ...BACKEND_SESSION_CONFIG.auth, session_path: "" },
      }),
    ).toThrow("Backend session authentication paths are missing");
    expect(() =>
      assertBackendSessionConfig({
        ...BACKEND_SESSION_CONFIG,
        auth: { ...BACKEND_SESSION_CONFIG.auth, csrf_header_name: "   " },
      }),
    ).toThrow("Backend session CSRF header name is invalid");
  });

  it("maps backend session JSON to profile identity", () => {
    expect(
      toBackendSessionUser({
        authenticated: true,
        is_admin: true,
        user: {
          id: "user-1",
          oidc_issuer: "https://issuer.example",
          oidc_sub: "subject-1",
          created_at: "2026-05-24T00:00:00Z",
        },
        profile: { display_name: "Arena User" },
        csrf_token: "csrf-token",
      }),
    ).toMatchObject({
      id: "user-1",
      oidcIssuer: "https://issuer.example",
      oidcSub: "subject-1",
      isAdmin: true,
      profile: {
        display_name: "Arena User",
        name: "Arena User",
        preferred_username: "subject-1",
        email: null,
      },
    });
    expect(toBackendSessionUser({ authenticated: false })).toBeNull();
  });
});
