// @vitest-environment jsdom

import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Footer } from "./Footer";

const useArenaAuthMock = vi.fn();

vi.mock("@/hooks/useArenaAuth", () => ({
  useArenaAuth: () => useArenaAuthMock(),
}));

function makeAuthState(overrides: Record<string, unknown> = {}) {
  return {
    authStatus: "unauthenticated",
    isLoading: false,
    isAuthenticated: false,
    user: null,
    accessToken: null,
    sessionError: null,
    headers: undefined,
    headersRef: { current: undefined },
    accessTokenRef: { current: null },
    signinRedirect: vi.fn(),
    signoutRedirect: vi.fn(),
    ...overrides,
  };
}

function renderFooter() {
  return render(
    <MemoryRouter>
      <Footer />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  useArenaAuthMock.mockReset();
  useArenaAuthMock.mockReturnValue(makeAuthState());
});

describe("Footer", () => {
  it("hides battle and profile links for anonymous users", () => {
    renderFooter();

    expect(screen.queryByRole("link", { name: "Battle" })).toBeNull();
    expect(screen.getByRole("link", { name: "Leaderboard" }).getAttribute("href")).toBe("/leaderboard");
    expect(screen.queryByRole("link", { name: "Profile" })).toBeNull();
  });

  it("shows battle, leaderboard, and profile links for authenticated users", () => {
    useArenaAuthMock.mockReturnValue(
      makeAuthState({ authStatus: "authenticated", isAuthenticated: true, accessToken: "token" }),
    );

    renderFooter();

    expect(screen.getByRole("link", { name: "Battle" }).getAttribute("href")).toBe("/battle/new");
    expect(screen.getByRole("link", { name: "Leaderboard" }).getAttribute("href")).toBe("/leaderboard");
    expect(screen.getByRole("link", { name: "Profile" }).getAttribute("href")).toBe("/onboarding");
  });
});
