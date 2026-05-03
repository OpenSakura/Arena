// @vitest-environment jsdom

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
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

beforeEach(() => {
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
        <LeaderboardRoute />
      </MemoryRouter>
    );

    expect(apiGetMock).toHaveBeenCalledWith("/leaderboard?method=elo");
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
    expect(screen.getByRole("link", { name: "Elo (baseline)" }).getAttribute("href")).toBe(
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
        <LeaderboardRoute />
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
        <LeaderboardRoute />
      </MemoryRouter>
    );

    expect(apiGetMock).toHaveBeenCalledWith("/leaderboard?method=bt&include_confidence=true");
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
    expect(screen.getByRole("link", { name: "Elo (baseline)" }).getAttribute("href")).toBe(
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
        <LeaderboardRoute />
      </MemoryRouter>
    );

    expect(apiGetMock).toHaveBeenCalledWith("/leaderboard?method=bt");

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
        <LeaderboardRoute />
      </MemoryRouter>
    );

    expect(apiGetMock).toHaveBeenCalledWith("/leaderboard?method=elo&include_confidence=true");

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
        <LeaderboardRoute />
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
        <LeaderboardRoute />
      </MemoryRouter>
    );

    expect(apiGetMock).toHaveBeenCalledWith("/leaderboard?method=bt");
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
});
