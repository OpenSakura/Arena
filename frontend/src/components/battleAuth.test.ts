import { describe, expect, it } from "vitest";

import { isBattleBootstrapReady } from "./battleAuth";

describe("isBattleBootstrapReady", () => {
  it("waits while auth state is loading", () => {
    expect(isBattleBootstrapReady("loading")).toBe(false);
  });

  it("allows bootstrap once auth state settles", () => {
    expect(isBattleBootstrapReady("authenticated")).toBe(true);
    expect(isBattleBootstrapReady("unauthenticated")).toBe(true);
  });
});
