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
  usePathname: () => "/",
}));

vi.mock("next-auth/react", () => ({
  useSession: () => useSessionMock(),
  signIn: (...args: unknown[]) => signInMock(...args),
  signOut: (...args: unknown[]) => signOutMock(...args),
}));

beforeEach(() => {
  useSessionMock.mockReset();
  signInMock.mockReset();
  signOutMock.mockReset();
});

describe("Header", () => {
  it("shows auth loading status while session state is loading", () => {
    useSessionMock.mockReturnValue({ data: null, status: "loading" });

    const { container } = renderHeader();

    // Loading state renders a pulse animation div, not text
    expect(container.querySelector(".animate-pulse")).toBeDefined();
    expect(screen.queryByRole("button", { name: "Login" })).toBeNull();
  });

  it("starts Authentik sign-in when login is clicked", async () => {
    useSessionMock.mockReturnValue({ data: null, status: "unauthenticated" });

    renderHeader();

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Login" }));

    expect(signInMock).toHaveBeenCalledWith("authentik");
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

    renderHeader();

    expect(screen.getByText("arena-user@example.com")).toBeDefined();

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Logout" }));

    expect(signOutMock).toHaveBeenCalledWith({ callbackUrl: "/" });
  });
});
