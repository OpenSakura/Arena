import { beforeEach, describe, expect, it, vi } from "vitest";

import { apiGet, apiPost } from "../lib/api";
import { asRecord, buildBattleAuthHeaders, loadOrCreateBattle, mergeBattleDelta } from "./battleViewUtils";

vi.mock("../lib/api", () => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
}));

const mockedApiGet = vi.mocked(apiGet);
const mockedApiPost = vi.mocked(apiPost);

beforeEach(() => {
  vi.clearAllMocks();
});

describe("buildBattleAuthHeaders", () => {
  it("returns undefined when no token is provided", () => {
    expect(buildBattleAuthHeaders()).toBeUndefined();
  });

  it("builds a bearer header from the current access token", () => {
    expect(buildBattleAuthHeaders("token-123")).toEqual({
      Authorization: "Bearer token-123",
    });
  });
});

describe("loadOrCreateBattle", () => {
  it("creates a new battle when battleId is new", async () => {
    mockedApiPost.mockResolvedValueOnce({ id: "battle-1" });

    await expect(loadOrCreateBattle("new")).resolves.toEqual({ id: "battle-1" });
    expect(mockedApiPost).toHaveBeenCalledWith("/battles", {}, { headers: undefined });
    expect(mockedApiGet).not.toHaveBeenCalled();
  });

  it("attaches bearer auth when creating with an access token", async () => {
    mockedApiPost.mockResolvedValueOnce({ id: "battle-2" });

    await loadOrCreateBattle("new", "token-123");

    expect(mockedApiPost).toHaveBeenCalledWith("/battles", {}, {
      headers: { Authorization: "Bearer token-123" },
    });
  });

  it("loads an existing battle using an encoded id", async () => {
    mockedApiGet.mockResolvedValueOnce({ id: "existing" });

    await expect(loadOrCreateBattle("battle/alpha beta")).resolves.toEqual({
      id: "existing",
    });

    expect(mockedApiGet).toHaveBeenCalledWith("/battles/battle%2Falpha%20beta", {
      headers: undefined,
    });
    expect(mockedApiPost).not.toHaveBeenCalled();
  });
});

describe("asRecord", () => {
  it("returns undefined for non-object values", () => {
    expect(asRecord(null)).toBeUndefined();
    expect(asRecord(undefined)).toBeUndefined();
    expect(asRecord("x")).toBeUndefined();
    expect(asRecord(12)).toBeUndefined();
  });

  it("returns objects as-is", () => {
    const payload = { side: "A", text_delta: "abc" };
    expect(asRecord(payload)).toBe(payload);
  });
});

describe("mergeBattleDelta", () => {
  it("appends normal stream chunks", () => {
    expect(mergeBattleDelta("hello", " world", false, null)).toBe("hello world");
  });

  it("replaces content on replay restart chunks", () => {
    expect(mergeBattleDelta("stale", "fresh", true, 0)).toBe("fresh");
    expect(mergeBattleDelta("stale", "fresh", true, null)).toBe("fresh");
  });

  it("keeps appending replay chunks after index zero", () => {
    expect(mergeBattleDelta("part-1", "-part-2", true, 1)).toBe("part-1-part-2");
  });
});
