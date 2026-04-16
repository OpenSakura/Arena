import { describe, expect, it } from "vitest";

import { hasBattleRefreshError, isBattleBootstrapReady } from "./battleAuth";

describe("isBattleBootstrapReady", () => {
  it("waits while auth state is loading", () => {
    expect(isBattleBootstrapReady("loading")).toBe(false);
  });

  it("allows bootstrap once auth state settles", () => {
    expect(isBattleBootstrapReady("authenticated")).toBe(true);
    expect(isBattleBootstrapReady("unauthenticated")).toBe(true);
  });
});

describe("hasBattleRefreshError", () => {
  it("detects known refresh-session failures", () => {
    expect(hasBattleRefreshError("RefreshTokenMissing")).toBe(true);
    expect(hasBattleRefreshError("RefreshTokenExpired")).toBe(true);
  });

  it("ignores unrelated or missing auth errors", () => {
    expect(hasBattleRefreshError(null)).toBe(false);
    expect(hasBattleRefreshError("OidcSigninFailed")).toBe(false);
  });
});
