// @vitest-environment jsdom

import { act, render, waitFor } from "@testing-library/react";
import { useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const navigateMock = vi.fn();
const setSearchParamsMock = vi.fn();
const searchParamsState = { current: new URLSearchParams() };

vi.mock("react-router-dom", () => ({
  useNavigate: () => navigateMock,
  useSearchParams: () => [searchParamsState.current, setSearchParamsMock] as const,
}));

vi.mock("@/hooks/useArenaAuth", () => ({
  useArenaAuth: vi.fn(),
}));

vi.mock("@/components/battleViewUtils", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/components/battleViewUtils")>();
  return {
    ...actual,
    loadOrCreateBattle: vi.fn(),
  };
});

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    apiPost: vi.fn(),
  };
});

vi.mock("@/lib/sse", () => ({
  streamSSE: vi.fn(),
}));

import { loadOrCreateBattle } from "@/components/battleViewUtils";
import { useArenaAuth } from "@/hooks/useArenaAuth";
import { apiPost } from "@/lib/api";
import { streamSSE } from "@/lib/sse";

import { __resetBattleRedirectCacheForTests, useBattle } from "./useBattle";

const mockedUseArenaAuth = vi.mocked(useArenaAuth);
const mockedLoadOrCreateBattle = vi.mocked(loadOrCreateBattle);
const mockedApiPost = vi.mocked(apiPost);
const mockedStreamSSE = vi.mocked(streamSSE);

type HookResult = ReturnType<typeof useBattle>;

function createAuthState(overrides: Record<string, unknown> = {}) {
  const authStatus = (overrides.authStatus as string | undefined) ?? "authenticated";

  return {
    authStatus,
    isLoading: false,
    isAuthenticated: authStatus === "authenticated",
    user: null,
    csrfToken: authStatus === "authenticated" ? "csrf-token" : null,
    sessionError: null,
    signinRedirect: vi.fn(),
    signoutRedirect: vi.fn(),
    ...overrides,
  };
}

function createBattle(overrides: Record<string, unknown> = {}) {
  const status = (overrides.status as string | undefined) ?? "completed";
  const hasOutputs = status === "completed";

  return {
    id: "battle-1",
    task_id: "task-1",
    source_text: "JP source",
    source_lang: "ja",
    target_lang: "zh",
    mode: "jp2zh_ab",
    status,
    retry_allowed: false,
    run_a: hasOutputs
      ? {
          id: "run-a",
          side: "A",
          output_text: "Alpha",
          stats: null,
          error_text: null,
        }
      : null,
    run_b: hasOutputs
      ? {
          id: "run-b",
          side: "B",
          output_text: "Beta",
          stats: null,
          error_text: null,
        }
      : null,
    ...overrides,
  };
}

function HookProbe({
  battleId,
  resultRef,
}: {
  battleId: string;
  resultRef: { current: HookResult | null };
}) {
  const result = useBattle(battleId);

  useEffect(() => {
    resultRef.current = result;
  }, [result, resultRef]);

  return null;
}

function renderUseBattle({ battleId = "new", search = "" }: { battleId?: string; search?: string } = {}) {
  searchParamsState.current = new URLSearchParams(search);
  const resultRef: { current: HookResult | null } = { current: null };

  const view = render(<HookProbe battleId={battleId} resultRef={resultRef} />);

  return { ...view, resultRef };
}

async function* emptyStream() {
  // Intentionally empty.
}

describe("useBattle", () => {
  beforeEach(() => {
    navigateMock.mockReset();
    setSearchParamsMock.mockReset();
    __resetBattleRedirectCacheForTests();
    mockedUseArenaAuth.mockReset();
    mockedLoadOrCreateBattle.mockReset();
    mockedApiPost.mockReset();
    mockedStreamSSE.mockReset();
    mockedStreamSSE.mockImplementation(emptyStream);
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("shows an inline auth error for /battle/new when unauthenticated", async () => {
    mockedUseArenaAuth.mockReturnValue(createAuthState({ authStatus: "unauthenticated" }));

    const { resultRef } = renderUseBattle({ battleId: "new" });

    await waitFor(() => {
      expect(resultRef.current?.state.errorText).toBe("Login required to start a battle.");
    });

    expect(mockedLoadOrCreateBattle).not.toHaveBeenCalled();
    expect(navigateMock).not.toHaveBeenCalled();
  });

  it("shows a session-expired inline error for /battle/new when the backend session failed", async () => {
    mockedUseArenaAuth.mockReturnValue(
      createAuthState({
        sessionError: "SessionExpired",
      }),
    );

    const { resultRef } = renderUseBattle({ battleId: "new" });

    await waitFor(() => {
      expect(resultRef.current?.state.errorText).toBe(
        "Your session has expired. Please log in again.",
      );
    });

    expect(mockedLoadOrCreateBattle).not.toHaveBeenCalled();
  });

  it("redirects to the created battle route after /battle/new bootstrap", async () => {
    mockedUseArenaAuth.mockReturnValue(createAuthState());
    mockedLoadOrCreateBattle.mockResolvedValueOnce(
      createBattle({ id: "battle/alpha beta" }),
    );

    renderUseBattle({ battleId: "new" });

    await waitFor(() => {
      expect(navigateMock).toHaveBeenCalledWith("/battle/battle%2Falpha%20beta");
    });
  });

  it("does not immediately re-bootstrap the redirected battle id after /battle/new resolves", async () => {
    mockedUseArenaAuth.mockReturnValue(createAuthState());
    mockedLoadOrCreateBattle.mockResolvedValueOnce(createBattle({ id: "battle-redirected" }));

    const view = renderUseBattle({ battleId: "new" });

    await waitFor(() => {
      expect(navigateMock).toHaveBeenCalledWith("/battle/battle-redirected");
      expect(mockedLoadOrCreateBattle).toHaveBeenCalledTimes(1);
    });

    view.rerender(<HookProbe battleId="battle-redirected" resultRef={{ current: null }} />);

    await waitFor(() => {
      expect(mockedLoadOrCreateBattle).toHaveBeenCalledTimes(1);
    });
  });

  it("hydrates the redirected battle id from the redirect cache on remount", async () => {
    mockedUseArenaAuth.mockReturnValue(createAuthState());
    mockedLoadOrCreateBattle.mockResolvedValueOnce(createBattle({ id: "battle-redirected" }));

    const firstRender = renderUseBattle({ battleId: "new" });

    await waitFor(() => {
      expect(navigateMock).toHaveBeenCalledWith("/battle/battle-redirected");
    });

    firstRender.unmount();
    renderUseBattle({ battleId: "battle-redirected" });

    await waitFor(() => {
      expect(mockedLoadOrCreateBattle).toHaveBeenCalledTimes(1);
    });
  });

  it("restarts /battle/new bootstrap when the restart query param changes", async () => {
    mockedUseArenaAuth.mockReturnValue(createAuthState());
    mockedLoadOrCreateBattle
      .mockResolvedValueOnce(createBattle({ id: "battle-1" }))
      .mockResolvedValueOnce(createBattle({ id: "battle-2" }));

    const view = renderUseBattle({ battleId: "new", search: "r=first" });

    await waitFor(() => {
      expect(mockedLoadOrCreateBattle).toHaveBeenCalledTimes(1);
    });

    searchParamsState.current = new URLSearchParams("r=second");
    view.rerender(<HookProbe battleId="new" resultRef={{ current: null }} />);

    await waitFor(() => {
      expect(mockedLoadOrCreateBattle).toHaveBeenCalledTimes(2);
    });
  });

  it("bootstraps new battles without browser bearer headers", async () => {
    mockedUseArenaAuth.mockReturnValue(createAuthState());
    mockedLoadOrCreateBattle.mockResolvedValueOnce(createBattle({ id: "battle-created" }));

    renderUseBattle({ battleId: "new" });

    await waitFor(() => {
      expect(mockedLoadOrCreateBattle).toHaveBeenCalledWith("new");
    });
  });

  it("starts SSE streams without browser auth header suppliers", async () => {
    mockedUseArenaAuth.mockReturnValue(createAuthState());
    mockedLoadOrCreateBattle.mockResolvedValueOnce(
      createBattle({ id: "battle-stream", status: "pending" }),
    );

    renderUseBattle({ battleId: "battle-stream" });

    await waitFor(() => {
      expect(mockedStreamSSE).toHaveBeenCalledTimes(1);
    });

    const [, init] = mockedStreamSSE.mock.calls[0] as [
      string,
      { getHeaders?: unknown; headers?: unknown },
    ];

    expect(init.headers).toBeUndefined();
    expect(init.getHeaders).toBeUndefined();
  });

  it("continues streaming across run.error until backend emits the terminal event", async () => {
    mockedUseArenaAuth.mockReturnValue(createAuthState());
    mockedLoadOrCreateBattle.mockResolvedValueOnce(
      createBattle({ id: "battle-stream", status: "pending", run_a: null, run_b: null }),
    );
    mockedStreamSSE.mockImplementationOnce(async function* () {
      yield {
        event: "run.error",
        data: { side: "A", error: "temporary failure" },
      };
      yield { event: "battle.started", data: { battle_id: "battle-stream" } };
      yield {
        event: "run.delta",
        data: { side: "A", text_delta: "Fresh output" },
      };
      yield { event: "battle.completed", data: { battle_id: "battle-stream" } };
    });

    const { resultRef } = renderUseBattle({ battleId: "battle-stream" });

    await waitFor(() => {
      expect(resultRef.current?.state.status).toBe("done");
    });

    expect(resultRef.current?.state.outA).toBe("Fresh output");
    expect(resultRef.current?.state.errorText).toBeNull();
  });

  it("refreshes terminal failed battles to pick up backend retry eligibility", async () => {
    mockedUseArenaAuth.mockReturnValue(createAuthState());
    mockedLoadOrCreateBattle
      .mockResolvedValueOnce(
        createBattle({ id: "battle-failed", status: "pending", run_a: null, run_b: null }),
      )
      .mockResolvedValueOnce(
        createBattle({ id: "battle-failed", status: "failed", retry_allowed: true, run_a: null, run_b: null }),
      );
    mockedStreamSSE.mockImplementationOnce(async function* () {
      yield { event: "battle.failed", data: { detail: "run_failed" } };
    });

    const { resultRef } = renderUseBattle({ battleId: "battle-failed" });

    await waitFor(() => {
      expect(resultRef.current?.state.status).toBe("failed");
      expect(resultRef.current?.canRetry).toBe(true);
    });

    expect(mockedLoadOrCreateBattle).toHaveBeenCalledTimes(2);
    expect(mockedLoadOrCreateBattle).toHaveBeenNthCalledWith(2, "battle-failed");
  });

  it("attempts reveal immediately after a successful vote submit", async () => {
    mockedUseArenaAuth.mockReturnValue(createAuthState());
    mockedLoadOrCreateBattle.mockResolvedValueOnce(createBattle());
    mockedApiPost
      .mockResolvedValueOnce({
        vote_id: "vote-1",
        battle_id: "battle-1",
        winner: "A",
        reveal: {
          A: { model_id: "model-a", display_name: "Model A" },
          B: { model_id: "model-b", display_name: "Model B" },
        },
      });

    const { resultRef } = renderUseBattle({ battleId: "battle-1" });

    await waitFor(() => {
      expect(resultRef.current?.state.status).toBe("done");
    });

    act(() => {
      resultRef.current?.dispatch({ type: "SET_WINNER", winner: "A" });
    });

    await waitFor(() => {
      expect(resultRef.current?.canVote).toBe(true);
    });

    await act(async () => {
      await resultRef.current?.handleVoteSubmit();
    });

    await waitFor(() => {
      expect(resultRef.current?.state.reveal).toEqual({
        A: { model_id: "model-a", display_name: "Model A" },
        B: { model_id: "model-b", display_name: "Model B" },
      });
    });

    expect(mockedApiPost).toHaveBeenNthCalledWith(
      1,
      "/battles/battle-1/vote",
      {
        winner: "A",
        rubric: { tags: [] },
        comment: null,
      },
    );
    expect(mockedApiPost.mock.calls[0]).toHaveLength(2);
    expect(mockedApiPost).toHaveBeenCalledTimes(1);
    expect(resultRef.current?.canVote).toBe(false);
  });

  it("navigates start-another-battle actions to /battle/new with a restart nonce", async () => {
    mockedUseArenaAuth.mockReturnValue(createAuthState());
    mockedLoadOrCreateBattle.mockResolvedValueOnce(createBattle({ id: "battle-existing" }));

    const { resultRef } = renderUseBattle({ battleId: "battle-existing" });

    await waitFor(() => {
      expect(resultRef.current?.state.resolvedBattleId).toBe("battle-existing");
    });

    act(() => {
      resultRef.current?.handleStartAnotherBattle();
    });

    expect(navigateMock).toHaveBeenCalledWith(expect.stringMatching(/^\/battle\/new\?r=/));
  });

  it("stream 401 maps to the session-expired stream error message", async () => {
    mockedUseArenaAuth.mockReturnValue(createAuthState());
    mockedLoadOrCreateBattle.mockResolvedValueOnce(
      createBattle({ id: "battle-stream", status: "pending" }),
    );
    mockedStreamSSE.mockImplementationOnce(async function* () {
      yield* [];
      throw new Error("SSE failed: 401");
    });

    const { resultRef } = renderUseBattle({ battleId: "battle-stream" });

    await waitFor(() => {
      expect(resultRef.current?.state.errorText).toBe(
        "Session expired or authentication failed. Please reload the page.",
      );
    });
  });
});
