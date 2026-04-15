// @vitest-environment jsdom

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { AnchorHTMLAttributes, ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Header } from "./Header";
import { ThemeProvider } from "./ThemeProvider";

function renderHeader() {
  return render(
    <ThemeProvider>
      <Header />
    </ThemeProvider>,
  );
}

const useSessionMock = vi.fn();
const usePathnameMock = vi.fn();
const useAdminAccessMock = vi.fn();
const signInMock = vi.fn();
const signOutMock = vi.fn();

type MockLinkProps = {
  href: string | { pathname?: string };
  children: ReactNode;
} & AnchorHTMLAttributes<HTMLAnchorElement>;

vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: any) => {
    const hrefValue = typeof href === "string" ? href : (href?.pathname ?? "/");
    return (
      <a href={hrefValue} {...props}>
        {children}
      </a>
    );
  },
}));

vi.mock("next/navigation", () => ({
  usePathname: () => usePathnameMock(),
}));

vi.mock("next-auth/react", () => ({
  useSession: () => useSessionMock(),
  signIn: (...args: unknown[]) => signInMock(...args),
  signOut: (...args: unknown[]) => signOutMock(...args),
}));

vi.mock("@/hooks/useAdminAccess", () => ({
  useAdminAccess: () => useAdminAccessMock(),
}));

beforeEach(() => {
  useSessionMock.mockReset();
  usePathnameMock.mockReset();
  useAdminAccessMock.mockReset();
  signInMock.mockReset();
  signOutMock.mockReset();

  usePathnameMock.mockReturnValue("/");
  useAdminAccessMock.mockReturnValue({ isAuthenticated: false, isAdmin: false, loading: false });
  window.history.replaceState({}, "", "/");
});

describe("Header", () => {
  it("shows auth loading status while session state is loading", () => {
    useSessionMock.mockReturnValue({ data: null, status: "loading" });

    const { container } = renderHeader();

    // Loading state renders a pulse animation div, not text
    expect(container.querySelector(".shimmer")).toBeDefined();
    expect(screen.queryByRole("button", { name: "Login" })).toBeNull();
  });

  it("starts OIDC sign-in when login is clicked", async () => {
    useSessionMock.mockReturnValue({ data: null, status: "unauthenticated" });

    renderHeader();

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Login" }));

    expect(signInMock).toHaveBeenCalledWith("oidc");
  });

  it("shows session identity and calls signOut for authenticated users", async () => {
    useSessionMock.mockReturnValue({
      data: {
        accessToken: "test-token",
        user: {
          email: "arena-user@example.com",
        },
      },
      status: "authenticated",
    });
    useAdminAccessMock.mockReturnValue({ isAuthenticated: true, isAdmin: false, loading: false });

    renderHeader();

    expect(screen.getByText("arena-user@example.com")).toBeDefined();

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Logout" }));

    expect(signOutMock).toHaveBeenCalledWith({ callbackUrl: "/" });
  });

  it("shows Battle nav link for anonymous users", () => {
    useSessionMock.mockReturnValue({ data: null, status: "unauthenticated" });

    renderHeader();

    expect(screen.getAllByText("Battle").length).toBeGreaterThanOrEqual(1);
  });

  it("shows Admin nav link for authenticated admin users", () => {
    useSessionMock.mockReturnValue({
      data: { accessToken: "admin-token", user: { email: "admin@example.com" } },
      status: "authenticated",
    });
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
    useSessionMock.mockReturnValue({ data: null, status: "unauthenticated" });
    useAdminAccessMock.mockReturnValue({ isAuthenticated: false, isAdmin: false, loading: false });

    renderHeader();

    expect(screen.queryByText("Admin")).toBeNull();
  });

  it("hides Admin nav link for authenticated non-admin users", () => {
    useSessionMock.mockReturnValue({
      data: { accessToken: "normal-token", user: { email: "normal@example.com" } },
      status: "authenticated",
    });
    useAdminAccessMock.mockReturnValue({ isAuthenticated: true, isAdmin: false, loading: false });

    renderHeader();

    expect(screen.queryByText("Admin")).toBeNull();
  });
});
