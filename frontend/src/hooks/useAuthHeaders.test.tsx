// @vitest-environment jsdom

import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthHeaders } from "./useAuthHeaders";

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
    csrfToken: null,
    sessionError: null,
    signinRedirect: vi.fn(),
    signoutRedirect: vi.fn(),
    ...overrides,
  };
}

function Probe() {
  const authHeaders = useAuthHeaders();
  const { authStatus, csrfToken, sessionError } = authHeaders;

  return (
    <div>
      <span>{authStatus}</span>
      <span data-testid="csrf-token">{csrfToken ?? "no-csrf"}</span>
      <span data-testid="session-error">{sessionError ?? "no-error"}</span>
      <span data-testid="has-authorization-headers">{String("headers" in authHeaders)}</span>
      <span data-testid="has-access-token">{String("accessToken" in authHeaders)}</span>
    </div>
  );
}

describe("useAuthHeaders", () => {
  beforeEach(() => {
    useArenaAuthMock.mockReset();
  });

  it("exposes session status and CSRF without bearer values when authenticated", () => {
    useArenaAuthMock.mockReturnValue(
      makeAuthState({
        authStatus: "authenticated",
        isAuthenticated: true,
        csrfToken: "csrf-token-1",
      }),
    );

    render(<Probe />);

    expect(screen.getByText("authenticated")).toBeDefined();
    expect(screen.getByTestId("csrf-token").textContent).toBe("csrf-token-1");
    expect(screen.getByTestId("session-error").textContent).toBe("no-error");
    expect(screen.getByTestId("has-authorization-headers").textContent).toBe("false");
    expect(screen.getByTestId("has-access-token").textContent).toBe("false");
  });

  it("exposes unauthenticated state without CSRF", async () => {
    useArenaAuthMock.mockReturnValue(makeAuthState());

    render(<Probe />);

    await waitFor(() => {
      expect(screen.getByText("unauthenticated")).toBeDefined();
    });
    expect(screen.getByTestId("csrf-token").textContent).toBe("no-csrf");
  });
});
