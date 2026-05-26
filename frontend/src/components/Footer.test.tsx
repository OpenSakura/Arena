// @vitest-environment jsdom

import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Footer } from "./Footer";

import { createTestI18n, TestI18nProvider } from "@/i18n/test-utils";
import type { i18n } from "i18next";

const useArenaAuthMock = vi.fn();

let i18nInstance: i18n;

vi.mock("@/hooks/useArenaAuth", () => ({
  useArenaAuth: () => useArenaAuthMock(),
}));

function makeAuthState(overrides: Record<string, unknown> = {}) {
  return {
    authStatus: "unauthenticated",
    isLoading: false,
    isAuthenticated: false,
    user: null,
    sessionError: null,
    signinRedirect: vi.fn(),
    signoutRedirect: vi.fn(),
    ...overrides,
  };
}

function renderFooter() {
  return render(
    <TestI18nProvider i18n={i18nInstance}>
      <MemoryRouter>
        <Footer />
      </MemoryRouter>
    </TestI18nProvider>
  );
}

beforeEach(async () => {
  i18nInstance = await createTestI18n("en");
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
      makeAuthState({ authStatus: "authenticated", isAuthenticated: true }),
    );

    renderFooter();

    expect(screen.getByRole("link", { name: "Battle" }).getAttribute("href")).toBe("/battle/new");
    expect(screen.getByRole("link", { name: "Leaderboard" }).getAttribute("href")).toBe("/leaderboard");
    expect(screen.getByRole("link", { name: "Profile" }).getAttribute("href")).toBe("/onboarding");
  });
});
