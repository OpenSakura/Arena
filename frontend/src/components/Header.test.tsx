// @vitest-environment jsdom

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Header } from "./Header";
import { ThemeProvider } from "./ThemeProvider";

import { createTestI18n, TestI18nProvider } from "@/i18n/test-utils";
import type { i18n } from "i18next";

const useArenaAuthMock = vi.fn();
const useAdminAccessMock = vi.fn();

let i18nInstance: i18n;

vi.mock("@/hooks/useArenaAuth", () => ({
  useArenaAuth: () => useArenaAuthMock(),
}));

vi.mock("@/hooks/useAdminAccess", () => ({
  useAdminAccess: () => useAdminAccessMock(),
}));

function renderHeader(initialEntries: string[] = ["/"]) {
  return render(
    <TestI18nProvider i18n={i18nInstance}>
      <MemoryRouter initialEntries={initialEntries}>
        <ThemeProvider>
          <Header />
        </ThemeProvider>
      </MemoryRouter>
    </TestI18nProvider>
  );
}

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

beforeEach(async () => {
  i18nInstance = await createTestI18n("en");
  useArenaAuthMock.mockReset();
  useAdminAccessMock.mockReset();

  useArenaAuthMock.mockReturnValue(makeAuthState());
  useAdminAccessMock.mockReturnValue({ isAuthenticated: false, isAdmin: false, loading: false });
  window.history.replaceState({}, "", "/");
});

describe("Header", () => {
  it("shows auth loading status while auth state is loading", () => {
    useArenaAuthMock.mockReturnValue(makeAuthState({ authStatus: "loading", isLoading: true }));

    const { container } = renderHeader();

    expect(container.querySelector(".shimmer")).toBeDefined();
    expect(screen.queryByRole("button", { name: "Login" })).toBeNull();
  });

  it("starts backend-session sign-in with the current route as returnTo when login is clicked", async () => {
    const signinRedirect = vi.fn();
    useArenaAuthMock.mockReturnValue(makeAuthState({ signinRedirect }));

    renderHeader(["/leaderboard?mode=recent#top"]);

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Login" }));

    expect(signinRedirect).toHaveBeenCalledWith({ state: { returnTo: "/leaderboard?mode=recent#top" } });
  });

  it("preserves callbackUrl query strings as same-origin route state instead of trusting them as redirects", async () => {
    const signinRedirect = vi.fn();
    useArenaAuthMock.mockReturnValue(makeAuthState({ signinRedirect }));

    renderHeader(["/?callbackUrl=%2Fadmin%2Fmodels"]);

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Login" }));

    expect(signinRedirect).toHaveBeenCalledWith({ state: { returnTo: "/?callbackUrl=%2Fadmin%2Fmodels" } });
  });

  it("shows backend-session identity and calls signoutRedirect for authenticated users", async () => {
    const signoutRedirect = vi.fn();
    useArenaAuthMock.mockReturnValue(
      makeAuthState({
        authStatus: "authenticated",
        isAuthenticated: true,
        user: {
          id: "user-1",
          oidcIssuer: "https://issuer.example",
          oidcSub: "subject-1",
          createdAt: "2026-05-24T00:00:00Z",
          isAdmin: false,
          profile: {
            display_name: "Arena User",
            preferred_username: "subject-1",
            email: null,
          },
        },
        signoutRedirect,
      }),
    );
    useAdminAccessMock.mockReturnValue({ isAuthenticated: true, isAdmin: false, loading: false });

    renderHeader();

    expect(screen.getByText("Arena User")).toBeDefined();

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Logout" }));

    expect(signoutRedirect).toHaveBeenCalledWith();
  });

  it("hides Battle nav link for anonymous users", () => {
    renderHeader();

    expect(screen.queryByText("Battle")).toBeNull();
  });

  it("shows Admin nav link for authenticated admin users", () => {
    useArenaAuthMock.mockReturnValue(
      makeAuthState({
        authStatus: "authenticated",
        isAuthenticated: true,
        user: {
          id: "admin-1",
          oidcIssuer: "https://issuer.example",
          oidcSub: "admin-subject",
          createdAt: "2026-05-24T00:00:00Z",
          isAdmin: true,
          profile: {},
        },
      }),
    );
    useAdminAccessMock.mockReturnValue({ isAuthenticated: true, isAdmin: true, loading: false });

    renderHeader();

    const adminLinks = screen.getAllByText("Admin");
    expect(adminLinks.length).toBeGreaterThanOrEqual(1);
    const desktopLink = adminLinks.find(
      (el) => el.closest("a")?.getAttribute("href") === "/admin/models",
    );
    expect(desktopLink).toBeDefined();
  });

  it("hides Admin nav link for anonymous users", () => {
    renderHeader();

    expect(screen.queryByText("Admin")).toBeNull();
  });

  it("hides Admin nav link for authenticated non-admin users", () => {
    useArenaAuthMock.mockReturnValue(
      makeAuthState({ authStatus: "authenticated", isAuthenticated: true }),
    );
    useAdminAccessMock.mockReturnValue({ isAuthenticated: true, isAdmin: false, loading: false });

    renderHeader();

    expect(screen.queryByText("Admin")).toBeNull();
  });
});
