import { describe, expect, it } from "vitest";

import { buildLeaderboardQuery, hasConfidenceIntervals, isEnabled } from "./leaderboard";

describe("isEnabled", () => {
  it("accepts canonical truthy values", () => {
    expect(isEnabled("1")).toBe(true);
    expect(isEnabled("true")).toBe(true);
    expect(isEnabled("TRUE")).toBe(true);
  });

  it("rejects empty and non-truthy values", () => {
    expect(isEnabled(undefined)).toBe(false);
    expect(isEnabled("")).toBe(false);
    expect(isEnabled("0")).toBe(false);
    expect(isEnabled("false")).toBe(false);
    expect(isEnabled("yes")).toBe(false);
  });
});

describe("buildLeaderboardQuery", () => {
  it("defaults to elo query", () => {
    expect(buildLeaderboardQuery()).toEqual({
      selectedMethod: "elo",
      includeConfidence: false,
      judgeType: "all",
      query: "/leaderboard?method=elo&judge_type=all",
    });
  });

  it("selects bt without CI by default", () => {
    expect(buildLeaderboardQuery({ method: "bt" })).toEqual({
      selectedMethod: "bt",
      includeConfidence: false,
      judgeType: "all",
      query: "/leaderboard?method=bt&judge_type=all",
    });
  });

  it("enables CI only for bt when flag is truthy", () => {
    expect(buildLeaderboardQuery({ method: "bt", include_confidence: "true" })).toEqual({
      selectedMethod: "bt",
      includeConfidence: true,
      judgeType: "all",
      query: "/leaderboard?method=bt&include_confidence=true&judge_type=all",
    });
  });

  it("normalizes numeric confidence flags for bt requests", () => {
    expect(buildLeaderboardQuery({ method: "bt", include_confidence: "1" })).toEqual({
      selectedMethod: "bt",
      includeConfidence: true,
      judgeType: "all",
      query: "/leaderboard?method=bt&include_confidence=true&judge_type=all",
    });
  });

  it("supports include_confidence for elo", () => {
    expect(buildLeaderboardQuery({ method: "elo", include_confidence: "true" })).toEqual({
      selectedMethod: "elo",
      includeConfidence: true,
      judgeType: "all",
      query: "/leaderboard?method=elo&include_confidence=true&judge_type=all",
    });
  });

  it("falls back to elo for unsupported methods", () => {
    expect(buildLeaderboardQuery({ method: "glicko", include_confidence: "1" })).toEqual({
      selectedMethod: "elo",
      includeConfidence: true,
      judgeType: "all",
      query: "/leaderboard?method=elo&include_confidence=true&judge_type=all",
    });
  });

  it("supports judge_type human", () => {
    expect(buildLeaderboardQuery({ judge_type: "human" })).toEqual({
      selectedMethod: "elo",
      includeConfidence: false,
      judgeType: "human",
      query: "/leaderboard?method=elo&judge_type=human",
    });
  });

  it("supports judge_type bot", () => {
    expect(buildLeaderboardQuery({ judge_type: "bot" })).toEqual({
      selectedMethod: "elo",
      includeConfidence: false,
      judgeType: "bot",
      query: "/leaderboard?method=elo&judge_type=bot",
    });
  });

  it("normalizes invalid judge_type to all", () => {
    expect(buildLeaderboardQuery({ judge_type: "invalid" })).toEqual({
      selectedMethod: "elo",
      includeConfidence: false,
      judgeType: "all",
      query: "/leaderboard?method=elo&judge_type=all",
    });
  });
});

describe("hasConfidenceIntervals", () => {
  it("returns true when any model has both bounds", () => {
    expect(
      hasConfidenceIntervals([
        { rating_lower: null, rating_upper: null },
        { rating_lower: 995.5, rating_upper: 1004.2 },
      ]),
    ).toBe(true);
  });

  it("returns false when bounds are missing", () => {
    expect(
      hasConfidenceIntervals([
        { rating_lower: 1000.1, rating_upper: null },
        { rating_lower: null, rating_upper: 1010.9 },
      ]),
    ).toBe(false);
  });
});
