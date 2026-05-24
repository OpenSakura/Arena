import { describe, expect, it } from "vitest";

import { hasBattleSessionError, isBattleBootstrapReady } from "./battleAuth";

describe("isBattleBootstrapReady", () => {
  it("waits while auth state is loading", () => {
    expect(isBattleBootstrapReady("loading")).toBe(false);
  });

  it("allows bootstrap once auth state settles", () => {
    expect(isBattleBootstrapReady("authenticated")).toBe(true);
    expect(isBattleBootstrapReady("unauthenticated")).toBe(true);
  });
});

describe("hasBattleSessionError", () => {
  it("detects backend session failures", () => {
    expect(hasBattleSessionError("SessionExpired")).toBe(true);
    expect(hasBattleSessionError("SessionBootstrapFailed")).toBe(true);
  });

  it("ignores unrelated or missing auth errors", () => {
    expect(hasBattleSessionError(null)).toBe(false);
    expect(hasBattleSessionError("")).toBe(false);
  });
});
