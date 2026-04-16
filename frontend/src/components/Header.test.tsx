// @vitest-environment jsdom

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Header } from "./Header";
import { ThemeProvider } from "./ThemeProvider";

const useArenaAuthMock = vi.fn();
const useAdminAccessMock = vi.fn();

vi.mock("@/hooks/useArenaAuth", () => ({
  useArenaAuth: () => useArenaAuthMock(),
}));

vi.mock("@/hooks/useAdminAccess", () => ({
  useAdminAccess: () => useAdminAccessMock(),
}));

function renderHeader(initialEntries: string[] = ["/"]) {
  return render(
    <MemoryRouter initialEntries={initialEntries}>
      <ThemeProvider>
        <Header />
      </ThemeProvider>
    </MemoryRouter>,
  );
}

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

beforeEach(() => {
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

  it("starts OIDC sign-in when login is clicked", async () => {
    const signinRedirect = vi.fn();
    useArenaAuthMock.mockReturnValue(makeAuthState({ signinRedirect }));

    renderHeader(["/leaderboard?mode=recent#top"]);

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Login" }));

    expect(signinRedirect).toHaveBeenCalledWith({ state: { returnTo: "/leaderboard?mode=recent#top" } });
  });

  it("uses callbackUrl as returnTo if provided in query string", async () => {
    const signinRedirect = vi.fn();
    useArenaAuthMock.mockReturnValue(makeAuthState({ signinRedirect }));

    renderHeader(["/?callbackUrl=%2Fadmin%2Fmodels"]);

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Login" }));

    expect(signinRedirect).toHaveBeenCalledWith({ state: { returnTo: "/admin/models" } });
  });

  it("shows session identity and calls signoutRedirect for authenticated users", async () => {
    const signoutRedirect = vi.fn();
    useArenaAuthMock.mockReturnValue(
      makeAuthState({
        authStatus: "authenticated",
        isAuthenticated: true,
        accessToken: "test-token",
        user: {
          profile: {
            email: "arena-user@example.com",
          },
        },
        signoutRedirect,
      }),
    );
    useAdminAccessMock.mockReturnValue({ isAuthenticated: true, isAdmin: false, loading: false });

    renderHeader();

    expect(screen.getByText("arena-user@example.com")).toBeDefined();

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Logout" }));

    expect(signoutRedirect).toHaveBeenCalledWith({ state: { returnTo: "/" } });
  });

  it("shows Battle nav link for anonymous users", () => {
    renderHeader();

    expect(screen.getAllByText("Battle").length).toBeGreaterThanOrEqual(1);
  });

  it("shows Admin nav link for authenticated admin users", () => {
    useArenaAuthMock.mockReturnValue(
      makeAuthState({ authStatus: "authenticated", isAuthenticated: true, accessToken: "admin-token" }),
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
      makeAuthState({ authStatus: "authenticated", isAuthenticated: true, accessToken: "normal-token" }),
    );
    useAdminAccessMock.mockReturnValue({ isAuthenticated: true, isAdmin: false, loading: false });

    renderHeader();

    expect(screen.queryByText("Admin")).toBeNull();
  });
});
