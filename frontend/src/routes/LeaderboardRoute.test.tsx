// @vitest-environment jsdom

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { TestI18nProvider, createTestI18n } from "@/i18n/test-utils";
import type { i18n } from "i18next";
import { beforeEach, describe, expect, it, vi } from "vitest";

const useArenaAuthMock = vi.fn();
vi.mock("@/hooks/useArenaAuth", () => {
  return {
    useArenaAuth: () => useArenaAuthMock(),
  };
});


import LeaderboardRoute from "./LeaderboardRoute";

const apiGetMock = vi.fn();

vi.mock("@/lib/api", () => ({
  apiGet: (...args: unknown[]) => apiGetMock(...args),
}));

let i18nInstance: i18n;

beforeEach(async () => {
  i18nInstance = await createTestI18n("en");
  useArenaAuthMock.mockReturnValue({ authStatus: "unauthenticated", signinRedirect: vi.fn() });
  apiGetMock.mockReset();
});

describe("LeaderboardRoute", () => {
  it("requests default Elo leaderboard and shows empty state", async () => {
    let resolveApi: (value: unknown) => void;
    const promise = new Promise((resolve) => {
      resolveApi = resolve;
    });
    apiGetMock.mockReturnValue(promise);

    render(
      <MemoryRouter initialEntries={["/leaderboard"]}>
        <TestI18nProvider i18n={i18nInstance}>
          <LeaderboardRoute />
        </TestI18nProvider>
      </MemoryRouter>
    );

    expect(apiGetMock).toHaveBeenCalledWith("/leaderboard?method=elo&judge_type=all");
    expect(screen.getByText("ELO")).toBeDefined();
    expect(screen.queryByText("No ratings yet")).toBeNull();

    // Resolve API
    resolveApi!({
      method: "elo",
      ci: false,
      bootstrap_rounds: null,
      models: [],
    });

    expect(await screen.findByText("No ratings yet")).toBeDefined();
    expect(screen.getByText("95% CI")).toBeDefined();
    expect(screen.getByRole("link", { name: "Elo" }).getAttribute("href")).toBe(
      "/leaderboard?method=elo"
    );
    expect(screen.getByRole("link", { name: "Show 95% CI" }).getAttribute("href")).toBe(
      "/leaderboard?method=elo&include_confidence=true"
    );
    expect(screen.queryByRole("columnheader", { name: "95% CI" })).toBeNull();
  });

  it("sends anonymous empty-state battle CTA through login", async () => {
    const signinRedirect = vi.fn();
    useArenaAuthMock.mockReturnValue({ authStatus: "unauthenticated", signinRedirect });
    apiGetMock.mockResolvedValue({
      method: "elo",
      ci: false,
      bootstrap_rounds: null,
      models: [],
    });

    render(
      <MemoryRouter initialEntries={["/leaderboard"]}>
        <TestI18nProvider i18n={i18nInstance}>
          <LeaderboardRoute />
        </TestI18nProvider>
      </MemoryRouter>
    );

    await userEvent.click(await screen.findByRole("button", { name: /Start a battle/i }));

    expect(signinRedirect).toHaveBeenCalledWith({ state: { returnTo: "/battle/new" } });
  });

  it("renders BT rows with confidence intervals when requested", async () => {
    let resolveApi: (value: unknown) => void;
    const promise = new Promise((resolve) => {
      resolveApi = resolve;
    });
    apiGetMock.mockReturnValue(promise);

    render(
      <MemoryRouter initialEntries={["/leaderboard?method=bt&include_confidence=true"]}>
        <TestI18nProvider i18n={i18nInstance}>
          <LeaderboardRoute />
        </TestI18nProvider>
      </MemoryRouter>
    );

    expect(apiGetMock).toHaveBeenCalledWith("/leaderboard?method=bt&include_confidence=true&judge_type=all");
    expect(screen.getByText("BT")).toBeDefined();

    resolveApi!({
      method: "bt",
      ci: true,
      bootstrap_rounds: 250,
      models: [
        {
          model_id: "model-a",
          display_name: "Model A",
          rating: 1210.36,
          rating_lower: 1188.9,
          rating_upper: 1234.2,
          games_played: 42,
        },
      ],
    });

    expect(await screen.findByText(/250 bootstrap rounds/)).toBeDefined();
    expect(screen.getByRole("link", { name: "Elo" }).getAttribute("href")).toBe(
      "/leaderboard?method=elo&include_confidence=true"
    );
    expect(screen.getByRole("link", { name: "Hide 95% CI" }).getAttribute("href")).toBe(
      "/leaderboard?method=bt"
    );
    expect(screen.getByRole("columnheader", { name: "95% CI" })).toBeDefined();
    expect(screen.getByText("Model A")).toBeDefined();
    expect(screen.getByText("1210.4")).toBeDefined();
    expect(screen.getByText(/1188\.9/)).toBeDefined();
  });

  it("shows the BT confidence toggle link when BT is selected", async () => {
    let resolveApi: (value: unknown) => void;
    const promise = new Promise((resolve) => {
      resolveApi = resolve;
    });
    apiGetMock.mockReturnValue(promise);

    render(
      <MemoryRouter initialEntries={["/leaderboard?method=bt"]}>
        <TestI18nProvider i18n={i18nInstance}>
          <LeaderboardRoute />
        </TestI18nProvider>
      </MemoryRouter>
    );

    expect(apiGetMock).toHaveBeenCalledWith("/leaderboard?method=bt&judge_type=all");

    resolveApi!({
      method: "bt",
      ci: false,
      bootstrap_rounds: null,
      models: [
        {
          model_id: "model-a",
          display_name: "Model A",
          rating: 1200,
          rating_lower: null,
          rating_upper: null,
          games_played: 5,
        },
      ],
    });

    expect(await screen.findByText("95% CI")).toBeDefined();
    expect(screen.getByRole("link", { name: "Show 95% CI" }).getAttribute("href")).toBe(
      "/leaderboard?method=bt&include_confidence=true"
    );
    expect(screen.queryByRole("columnheader", { name: "95% CI" })).toBeNull();
  });

  it("requests Elo confidence intervals when enabled", async () => {
    let resolveApi: (value: unknown) => void;
    const promise = new Promise((resolve) => {
      resolveApi = resolve;
    });
    apiGetMock.mockReturnValue(promise);

    render(
      <MemoryRouter initialEntries={["/leaderboard?method=elo&include_confidence=true"]}>
        <TestI18nProvider i18n={i18nInstance}>
          <LeaderboardRoute />
        </TestI18nProvider>
      </MemoryRouter>
    );

    expect(apiGetMock).toHaveBeenCalledWith("/leaderboard?method=elo&include_confidence=true&judge_type=all");

    resolveApi!({
      method: "elo",
      ci: true,
      bootstrap_rounds: 200,
      models: [
        {
          model_id: "model-a",
          display_name: "Model A",
          rating: 1000,
          rating_lower: 980,
          rating_upper: 1020,
          games_played: 10,
        },
      ],
    });

    expect(await screen.findByText(/200 bootstrap rounds/)).toBeDefined();
    expect(screen.getByText("ELO")).toBeDefined();
    expect(screen.getByRole("columnheader", { name: "95% CI" })).toBeDefined();
  });

  it("shows failed to load leaderboard message when fetch fails", async () => {
    let rejectApi: (error: unknown) => void;
    const promise = new Promise((_, reject) => {
      rejectApi = reject;
    });
    apiGetMock.mockReturnValue(promise);

    render(
      <MemoryRouter initialEntries={["/leaderboard?method=bt"]}>
        <TestI18nProvider i18n={i18nInstance}>
          <LeaderboardRoute />
        </TestI18nProvider>
      </MemoryRouter>
    );

    rejectApi!(new Error("backend unavailable"));

    expect(await screen.findByText("backend unavailable")).toBeDefined();
    expect(screen.queryByText("No ratings yet")).toBeNull();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("requests BT without confidence using correct endpoint without model_ratings", async () => {
    let resolveApi: (value: unknown) => void;
    const promise = new Promise((resolve) => {
      resolveApi = resolve;
    });
    apiGetMock.mockReturnValue(promise);

    render(
      <MemoryRouter initialEntries={["/leaderboard?method=bt"]}>
        <TestI18nProvider i18n={i18nInstance}>
          <LeaderboardRoute />
        </TestI18nProvider>
      </MemoryRouter>
    );

    expect(apiGetMock).toHaveBeenCalledWith("/leaderboard?method=bt&judge_type=all");
    expect(apiGetMock).not.toHaveBeenCalledWith(
      expect.stringContaining("include_confidence")
    );

    resolveApi!({
      method: "bt",
      ci: false,
      bootstrap_rounds: null,
      models: [
        {
          model_id: "model-a",
          display_name: "Model A",
          rating: 1050,
          rating_lower: null,
          rating_upper: null,
          games_played: 8,
        },
      ],
    });

    expect(await screen.findByText("Model A")).toBeDefined();
    expect(screen.getByText("BT")).toBeDefined();
    expect(screen.queryByRole("columnheader", { name: "95% CI" })).toBeNull();
  });

  it("offers an exclude-refusals toggle that enables the filter", async () => {
    let resolveApi: (value: unknown) => void;
    apiGetMock.mockReturnValue(new Promise((resolve) => { resolveApi = resolve; }));

    render(
      <MemoryRouter initialEntries={["/leaderboard"]}>
        <TestI18nProvider i18n={i18nInstance}>
          <LeaderboardRoute />
        </TestI18nProvider>
      </MemoryRouter>
    );

    expect(apiGetMock).toHaveBeenCalledWith("/leaderboard?method=elo&judge_type=all");

    resolveApi!({ method: "elo", ci: false, bootstrap_rounds: null, models: [] });
    await screen.findByText("No ratings yet");

    expect(screen.getByText("No refusals")).toBeDefined();
    // Off by default → the toggle link turns the filter on.
    expect(
      screen.getByRole("link", { name: "Exclude refusal votes" }).getAttribute("href")
    ).toBe("/leaderboard?method=elo&exclude_refusals=true");
  });

  it("requests the leaderboard with refusals excluded and preserves other filters", async () => {
    let resolveApi: (value: unknown) => void;
    apiGetMock.mockReturnValue(new Promise((resolve) => { resolveApi = resolve; }));

    render(
      <MemoryRouter initialEntries={["/leaderboard?method=bt&judge_type=human&exclude_refusals=true"]}>
        <TestI18nProvider i18n={i18nInstance}>
          <LeaderboardRoute />
        </TestI18nProvider>
      </MemoryRouter>
    );

    expect(apiGetMock).toHaveBeenCalledWith(
      "/leaderboard?method=bt&judge_type=human&exclude_refusals=true"
    );

    resolveApi!({ method: "bt", ci: false, bootstrap_rounds: null, models: [] });
    await screen.findByText("No ratings yet");

    // On → the toggle link turns the filter off while keeping method + judge_type.
    expect(
      screen.getByRole("link", { name: "Show refusal votes" }).getAttribute("href")
    ).toBe("/leaderboard?method=bt&judge_type=human");
  });
});

  it("query: changing filters updates query params correctly in Chinese locale too", async () => {
    let resolveApi: (value: unknown) => void;
    apiGetMock.mockReturnValue(new Promise((resolve) => { resolveApi = resolve; }));

    const zhI18n = await createTestI18n("zh");

    render(
      <TestI18nProvider i18n={zhI18n}>
        <MemoryRouter initialEntries={["/leaderboard"]}>
          <Routes><Route path="/leaderboard" element={<LeaderboardRoute />} /></Routes>
        </MemoryRouter>
      </TestI18nProvider>
    );

    // Initial query
    expect(apiGetMock).toHaveBeenCalledWith("/leaderboard?method=elo&judge_type=all");

    // Wait for empty state to load
    resolveApi!({ method: "elo", ci: false, bootstrap_rounds: null, models: [] });
    expect(await screen.findByText("暂无评分")).toBeDefined();
    
    // Check Chinese UI labels
    expect(screen.getByText("排行榜")).toBeDefined();
    
    // Instead of clicking and waiting for router, just assert the generated hrefs
    expect(screen.getByRole("link", { name: "Bradley-Terry" }).getAttribute("href")).toBe("/leaderboard?method=bt");
    expect(screen.getByRole("link", { name: "人工投票" }).getAttribute("href")).toBe("/leaderboard?method=elo&judge_type=human");
    expect(screen.getByRole("link", { name: "显示 95% CI" }).getAttribute("href")).toBe("/leaderboard?method=elo&include_confidence=true");
  });
