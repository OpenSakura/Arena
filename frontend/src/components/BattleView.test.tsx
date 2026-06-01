// @vitest-environment jsdom

import { render, screen, waitFor } from "@testing-library/react";
import { createTestI18n, TestI18nProvider } from "@/i18n/test-utils";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { routerFutureConfig } from "@/router";
import { BattleView } from "./BattleView";

const useBattleMock = vi.fn();
let testI18n: Awaited<ReturnType<typeof createTestI18n>>;

vi.mock("@/hooks/useBattle", () => ({
  useBattle: (...args: unknown[]) => useBattleMock(...args),
}));

function renderBattleView(
  battleId = "new",
  i18nInstance = testI18n,
) {
  const router = (
    <MemoryRouter initialEntries={[`/battle/${battleId}`]} future={routerFutureConfig}>
      <Routes>
        <Route path="/battle/:battleId" element={<BattleView battleId={battleId} />} />
      </Routes>
    </MemoryRouter>
  );

  return render(<TestI18nProvider i18n={i18nInstance}>{router}</TestI18nProvider>);
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
      adminRevealData: null,
      adminRevealed: { A: false, B: false },
      ...stateOverrides,
    },
    dispatch: vi.fn(),
    isAuthed: true,
    authStatus: "authenticated",
    hasSessionError: false,
    canVote: true,
    canShowVoteControls: true,
    isValidVoteStatus: true,
    isVoteCooldownActive: false,
    canRetry: false,
    voteSubmitted: false,
    statusLabel: "Loading...",
    handleVoteSubmit: vi.fn(),
    handleRetry: vi.fn(),
    handleStartAnotherBattle: vi.fn(),
    ...Object.fromEntries(Object.entries(overrides).filter(([key]) => key !== "state")),
  };
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

describe("BattleView", () => {
  beforeEach(async () => {
    testI18n = await createTestI18n("en");
  });

  it("localizes voting", async () => {
    useBattleMock.mockReturnValue(
      createUseBattleState({
        state: {
          status: "done",
          jpSource: "JP source",
          outA: "Alpha",
          outB: "Beta",
        },
        statusLabel: "Complete",
      }),
    );

    const testZh = await createTestI18n("zh");

    renderBattleView("new", testZh);

    expect(screen.getAllByText(/哪个翻译更好/i).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: /模型 A 更好/i })).toBeDefined();
    expect(screen.getByRole("button", { name: /不分胜负/i })).toBeDefined();
    expect(screen.getByRole("button", { name: /模型 B 更好/i })).toBeDefined();

    expect(screen.getByText("JP source")).toBeDefined();
    expect(screen.getByText("Alpha")).toBeDefined();
    expect(screen.getByText("Beta")).toBeDefined();
  });

  it("error", async () => {
    useBattleMock.mockReturnValue(
      createUseBattleState({
        state: {
          status: "error",
          errorText: "Test Error",
          resolvedBattleId: null
        },
        statusLabel: "Test Error",
      }),
    );

    renderBattleView("new");
    expect(screen.getByText(/Unable to load battle/i)).toBeDefined();
    expect(screen.getByText(/Test Error/i)).toBeDefined();
  });

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

    renderBattleView("new");

    await screen.findByText("JP source");
    await screen.findByText("Alpha");
    await screen.findByText("Beta");
    expect(screen.getByRole("button", { name: "Knowledge" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Cultural" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Voice" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Terminology" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Refusal" })).toBeDefined();

    const culturalButton = screen.getByRole("button", { name: "Cultural" });
    expect(culturalButton.getAttribute("aria-describedby")).toBe("tooltip-cultural");
    const culturalTooltip = document.getElementById("tooltip-cultural");
    expect(culturalTooltip).toBeDefined();
    expect(culturalTooltip?.className).toContain("group-hover/rubric:opacity-100");
    expect(culturalTooltip?.className).not.toContain("group-hover:opacity-100");
    expect(culturalTooltip?.textContent).toContain("Proper handling");

    const refusalButton = screen.getByRole("button", { name: "Refusal" });
    expect(refusalButton.getAttribute("aria-describedby")).toBe("tooltip-refusal");
    const refusalTooltip = document.getElementById("tooltip-refusal");
    expect(refusalTooltip).toBeDefined();
    expect(refusalTooltip?.className).toContain("group-hover/rubric:opacity-100");
    expect(refusalTooltip?.className).not.toContain("group-hover:opacity-100");
    expect(refusalTooltip?.textContent).toContain("Refused to translate or provide a response.");

    const user = userEvent.setup();
    const option = screen.getByText(/Model A is better/i).closest("button");
    if (!option) throw new Error("Vote option not found");

    await user.click(culturalButton);
    expect(dispatch).toHaveBeenCalledWith({ type: "TOGGLE_RUBRIC_TAG", tag: "cultural" });

    await user.click(refusalButton);
    expect(dispatch).toHaveBeenCalledWith({ type: "TOGGLE_RUBRIC_TAG", tag: "refusal" });

    await user.click(option);
    await user.click(screen.getByRole("button", { name: "Submit Vote" }));

    expect(dispatch).toHaveBeenCalledWith({ type: "SET_WINNER", winner: "A" });
    expect(handleVoteSubmit).toHaveBeenCalledTimes(1);
  });

  it("keeps source text and both model outputs in one responsive comparison grid", async () => {
    useBattleMock.mockReturnValue(
      createUseBattleState({
        state: {
          outA: "Alpha",
          outB: "Beta",
          status: "done",
        },
        statusLabel: "Complete",
      }),
    );

    renderBattleView("new");

    await screen.findByText("JP source");
    await screen.findByText("Alpha");
    await screen.findByText("Beta");

    const comparisonGrid = screen.getByRole("region", { name: "Battle comparison" });
    expect(comparisonGrid.classList.contains("grid")).toBe(true);
    expect(comparisonGrid.classList.contains("grid-cols-1")).toBe(true);
    expect(comparisonGrid.classList.contains("lg:grid-cols-3")).toBe(true);

    const sourcePanel = screen.getByText(/Source text/i).closest("section");
    const modelAPanel = screen.getByText("Model A").closest("section");
    const modelBPanel = screen.getByText("Model B").closest("section");
    const comparisonPanels = Array.from(comparisonGrid.children);

    expect(sourcePanel).toBeDefined();
    expect(modelAPanel).toBeDefined();
    expect(modelBPanel).toBeDefined();
    expect(comparisonPanels).toContain(sourcePanel);
    expect(comparisonPanels).toContain(modelAPanel);
    expect(comparisonPanels).toContain(modelBPanel);
  });

  it("renders metallic Thinking text while model output is pending", async () => {
    useBattleMock.mockReturnValue(
      createUseBattleState({
        state: {
          status: "streaming",
          outA: "",
          outB: "",
        },
        statusLabel: "Thinking...",
      }),
    );

    renderBattleView("new");

    const thinkingNodes = await screen.findAllByText("Thinking...");
    expect(thinkingNodes).toHaveLength(4);
    expect(thinkingNodes.every((node) => node.classList.contains("thinking-metal"))).toBe(true);
    expect(screen.getByText(/Source text/i).closest("section")?.textContent).not.toContain("Thinking...");
    expect(screen.queryByText("Translating...")).toBeNull();
  });

  it("shows login error when unauthenticated and loading battle fails", async () => {
    useBattleMock.mockReturnValue(
      createUseBattleState({
        state: { status: "error", errorText: "Login required to view battles.", resolvedBattleId: null },
        isAuthed: false,
        authStatus: "unauthenticated",
        canVote: false,
        statusLabel: "Error",
      }),
    );

    renderBattleView("new");

    await screen.findByText("Unable to load battle");
    expect(screen.getByText("Login required to view battles.")).toBeDefined();
  });

  it("renders voting controls during streaming", async () => {
    useBattleMock.mockReturnValue(createUseBattleState({
      state: {
        resolvedBattleId: "battle-1",
        jpSource: "JP source",
        jpSourceLang: "JA",
        targetLang: "ZH",
        outA: "Alpha",
        outB: "Beta",
        status: "streaming",
        errorText: null,
        winner: null,
        rubricTags: [],
        comment: "",
        submittingVote: false,
        voteId: null,
        reveal: null,
        adminRevealData: null,
        adminRevealed: { A: false, B: false },
        retryCount: 0,
        retryAllowed: false,
        streamStartTime: Date.now() - 15000,
      },
    }));

    renderBattleView("battle-1");

    expect(screen.getByRole("region", { name: "Battle comparison" }).parentElement?.className).toContain("pb-36");

    const voteRegion = screen.getByRole("region", { name: "Voting area" });
    expect(voteRegion).toBeDefined();

    expect(voteRegion.className).toContain("fixed");
    expect(voteRegion.className).toContain("bottom-0");
    expect(voteRegion.className).toContain("group");
    expect(voteRegion.textContent).toContain("Cast Your Vote");

    const expandingContent = voteRegion.querySelector(".max-h-28");
    expect(expandingContent).toBeDefined();
    expect(expandingContent?.className).toContain("group-hover:max-h-[85vh]");

    expect(screen.getByRole("button", { name: "Submit Vote" })).toBeDefined();
    const tooltips = screen.getAllByRole("tooltip");
    expect(tooltips).toHaveLength(10);
    expect(tooltips.every((tooltip) => tooltip.className.includes("group-hover/rubric:opacity-100"))).toBe(true);
    expect(tooltips.every((tooltip) => !tooltip.className.includes("group-hover:opacity-100"))).toBe(true);
  });

  it("shows expired-session messaging when the backend session failed", async () => {
    useBattleMock.mockReturnValue(
      createUseBattleState({
        state: { status: "done" },
        hasSessionError: true,
        canVote: false,
        statusLabel: "Complete",
      }),
    );

    renderBattleView("new");

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

  it("keeps admin model identities hidden until the reveal control is clicked", async () => {
    const dispatch = vi.fn();
    useBattleMock.mockReturnValue(
      createUseBattleState({
        state: {
          status: "done",
          adminRevealData: {
            A: { model_id: "secret-a", display_name: "Secret Model A" },
            B: { model_id: "secret-b", display_name: "Secret Model B" },
          },
        },
        dispatch,
        statusLabel: "Complete",
      }),
    );

    renderBattleView("battle-admin-reveal");

    expect(screen.queryByText("Secret Model A")).toBeNull();
    expect(screen.queryByText("Secret Model B")).toBeNull();

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Reveal Model A identity" }));

    expect(dispatch).toHaveBeenCalledWith({ type: "ADMIN_REVEAL_SIDE", side: "A" });
  });

  it("renders admin-revealed model names without using the vote reveal section", async () => {
    useBattleMock.mockReturnValue(
      createUseBattleState({
        state: {
          status: "done",
          adminRevealData: {
            A: { model_id: "secret-a", display_name: "Secret Model A" },
            B: { model_id: "secret-b", display_name: "Secret Model B" },
          },
          adminRevealed: { A: true, B: false },
        },
        statusLabel: "Complete",
      }),
    );

    renderBattleView("battle-admin-revealed");

    expect(screen.getByText("Secret Model A")).toBeDefined();
    expect(screen.queryByText("Secret Model B")).toBeNull();
    expect(screen.queryByText("Models Revealed")).toBeNull();
    expect(screen.getByRole("button", { name: "Reveal Model B identity" })).toBeDefined();
  });

  it("does not show admin reveal controls when admin reveal data is absent", async () => {
    useBattleMock.mockReturnValue(
      createUseBattleState({
        state: {
          status: "done",
          outA: "Alpha",
          outB: "Beta",
        },
        statusLabel: "Complete",
      }),
    );

    renderBattleView("battle-no-admin-reveal");

    expect(screen.queryByRole("button", { name: /Reveal Model [AB] identity/ })).toBeNull();
  });
});
