// @vitest-environment jsdom

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi, beforeEach } from "vitest";

import { createTestI18n, TestI18nProvider } from "@/i18n/test-utils";
import type { i18n } from "i18next";

const useArenaAuthMock = vi.fn();
let i18nInstance: i18n;

vi.mock("@/hooks/useArenaAuth", () => {
  return {
    useArenaAuth: () => useArenaAuthMock(),
  };
});


import HomePage from "./HomePage";

describe("HomePage", () => {
  beforeEach(async () => {
    i18nInstance = await createTestI18n("en");
    useArenaAuthMock.mockReturnValue({ authStatus: "authenticated", signinRedirect: vi.fn() });
  });
  it("renders the main heading", () => {
    render(
      <TestI18nProvider i18n={i18nInstance}>
        <MemoryRouter>
          <HomePage />
        </MemoryRouter>
      </TestI18nProvider>
    );

    expect(screen.getByRole("heading", { name: /Open\s*Sakura\s*Arena/i })).toBeDefined();
  });

  it("contains CTA links to battle and leaderboard", () => {
    render(
      <TestI18nProvider i18n={i18nInstance}>
        <MemoryRouter>
          <HomePage />
        </MemoryRouter>
      </TestI18nProvider>
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
      <TestI18nProvider i18n={i18nInstance}>
        <MemoryRouter>
          <HomePage />
        </MemoryRouter>
      </TestI18nProvider>
    );

    await userEvent.click(screen.getByRole("button", { name: /Start a Battle/i }));

    expect(signinRedirect).toHaveBeenCalledWith({ state: { returnTo: "/battle/new" } });
    expect(screen.queryByRole("link", { name: /Start a Battle/i })).toBeNull();
  });

  it("shows English home hero text for en locale (English)", async () => {
    const enI18n = await createTestI18n("en");
    render(
      <TestI18nProvider i18n={enI18n}>
        <MemoryRouter>
          <HomePage />
        </MemoryRouter>
      </TestI18nProvider>
    );

    expect(screen.getByText(/Pairwise, blind comparisons of JP>ZH light-novel style translations/i)).toBeDefined();
    expect(screen.getByRole("link", { name: /Start a Battle/i })).toBeDefined();
  });

  it("shows Chinese home hero text for zh locale (Chinese)", async () => {
    const zhI18n = await createTestI18n("zh");
    render(
      <TestI18nProvider i18n={zhI18n}>
        <MemoryRouter>
          <HomePage />
        </MemoryRouter>
      </TestI18nProvider>
    );

    expect(screen.getByText(/对日文到中文的轻小说风格翻译进行双盲对比评价/i)).toBeDefined();
    expect(screen.getByRole("link", { name: /开始对战/i })).toBeDefined();
  });
});
