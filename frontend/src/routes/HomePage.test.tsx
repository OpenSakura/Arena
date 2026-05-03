// @vitest-environment jsdom

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi, beforeEach } from "vitest";

const useArenaAuthMock = vi.fn();
vi.mock("@/hooks/useArenaAuth", () => {
  return {
    useArenaAuth: () => useArenaAuthMock(),
  };
});


import HomePage from "./HomePage";

describe("HomePage", () => {
  beforeEach(() => {
    useArenaAuthMock.mockReturnValue({ authStatus: "authenticated", signinRedirect: vi.fn() });
  });
  it("renders the main heading", () => {
    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>
    );

    expect(screen.getByRole("heading", { name: /Open\s*Sakura\s*Arena/i })).toBeDefined();
  });

  it("contains CTA links to battle and leaderboard", () => {
    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>
    );

    const startBattleLink = screen.getByRole("link", { name: /Start a Battle/i });
    expect(startBattleLink.getAttribute("href")).toBe("/battle/new");

    const viewLeaderboardLink = screen.getByRole("link", { name: /View Leaderboard/i });
    expect(viewLeaderboardLink.getAttribute("href")).toBe("/leaderboard");
  });

  it("redirects anonymous users to login from the battle CTA", async () => {
    const signinRedirect = vi.fn();
    useArenaAuthMock.mockReturnValue({ authStatus: "unauthenticated", signinRedirect });

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>
    );

    await userEvent.click(screen.getByRole("button", { name: /Start a Battle/i }));

    expect(signinRedirect).toHaveBeenCalledWith({ state: { returnTo: "/battle/new" } });
    expect(screen.queryByRole("link", { name: /Start a Battle/i })).toBeNull();
  });
});
