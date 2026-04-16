// @vitest-environment jsdom

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { routerFutureConfig } from "@/router";
import { BattleView } from "./BattleView";

const useBattleMock = vi.fn();

vi.mock("@/hooks/useBattle", () => ({
  useBattle: (...args: unknown[]) => useBattleMock(...args),
}));

function renderBattleView(battleId = "new") {
  return render(
    <MemoryRouter initialEntries={[`/battle/${battleId}`]} future={routerFutureConfig}>
      <Routes>
        <Route path="/battle/:battleId" element={<BattleView battleId={battleId} />} />
      </Routes>
    </MemoryRouter>,
  );
}

function createUseBattleState(overrides: Record<string, unknown> = {}) {
  const stateOverrides = (overrides.state as Record<string, unknown> | undefined) ?? {};

  return {
    state: {
      resolvedBattleId: "battle-123",
      jpSource: "JP source",
      jpSourceLang: "JA",
      targetLang: "ZH",
      outA: "",
      outB: "",
      status: "loading",
      errorText: null,
      winner: null,
      rubricTags: [],
      comment: "",
      submittingVote: false,
      voteId: null,
      reveal: null,
      revealLoading: false,
      ...stateOverrides,
    },
    dispatch: vi.fn(),
    isAuthed: true,
    authStatus: "authenticated",
    hasRefreshError: false,
    canVote: true,
    canReveal: false,
    canRetry: false,
    voteSubmitted: false,
    statusLabel: "Loading...",
    handleVoteSubmit: vi.fn(),
    handleReveal: vi.fn(),
    handleRetry: vi.fn(),
    handleStartAnotherBattle: vi.fn(),
    ...Object.fromEntries(Object.entries(overrides).filter(([key]) => key !== "state")),
  };
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

beforeEach(() => {
  useBattleMock.mockReset();
});

describe("BattleView", () => {
  it("renders streamed outputs and submits a vote", async () => {
    const dispatch = vi.fn();
    const handleVoteSubmit = vi.fn();

    useBattleMock.mockReturnValue(
      createUseBattleState({
        state: {
          outA: "Alpha",
          outB: "Beta",
          status: "done",
        },
        dispatch,
        canVote: true,
        statusLabel: "Complete",
        handleVoteSubmit,
      }),
    );

    renderBattleView();

    await screen.findByText("JP source");
    await screen.findByText("Alpha");
    await screen.findByText("Beta");

    const user = userEvent.setup();
    const option = screen.getByText(/Model A is better/i).closest("button");
    if (!option) throw new Error("Vote option not found");
    await user.click(option);
    await user.click(screen.getByRole("button", { name: "Submit Vote" }));

    expect(dispatch).toHaveBeenCalledWith({ type: "SET_WINNER", winner: "A" });
    expect(handleVoteSubmit).toHaveBeenCalledTimes(1);
  });

  it("shows anonymous login messaging after completion", async () => {
    useBattleMock.mockReturnValue(
      createUseBattleState({
        state: { status: "done" },
        isAuthed: false,
        authStatus: "unauthenticated",
        canVote: false,
        statusLabel: "Complete",
      }),
    );

    renderBattleView();

    await screen.findByText("Login to Vote");
    expect(screen.getByText(/please log in/i)).toBeDefined();
  });

  it("shows refresh-expired messaging when session refresh failed", async () => {
    useBattleMock.mockReturnValue(
      createUseBattleState({
        state: { status: "done" },
        hasRefreshError: true,
        canVote: false,
        statusLabel: "Complete",
      }),
    );

    renderBattleView();

    await screen.findByText("Session Expired");
    expect(screen.getByText(/Your session has expired. Please log in again./i)).toBeDefined();
  });

  it("shows retry/start another controls for failed battles", async () => {
    const handleRetry = vi.fn();
    const handleStartAnotherBattle = vi.fn();

    useBattleMock.mockReturnValue(
      createUseBattleState({
        state: { status: "failed" },
        canRetry: true,
        statusLabel: "Failed",
        handleRetry,
        handleStartAnotherBattle,
      }),
    );

    renderBattleView("battle-3");

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Retry Battle" }));
    await user.click(screen.getByRole("button", { name: "Start another battle" }));

    expect(handleRetry).toHaveBeenCalledTimes(1);
    expect(handleStartAnotherBattle).toHaveBeenCalledTimes(1);
  });

  it("renders revealed model identities", async () => {
    useBattleMock.mockReturnValue(
      createUseBattleState({
        state: {
          status: "done",
          winner: "A",
          voteId: "vote-1",
          reveal: {
            A: { model_id: "model-a", display_name: "Model A" },
            B: { model_id: "model-b", display_name: "Model B" },
          },
        },
        voteSubmitted: true,
        statusLabel: "Complete",
      }),
    );

    renderBattleView("battle-reveal");

    await waitFor(() => {
      expect(screen.getAllByText("Model A").length).toBeGreaterThanOrEqual(1);
    });
    expect(screen.getAllByText("Model B").length).toBeGreaterThanOrEqual(1);
  });
});
