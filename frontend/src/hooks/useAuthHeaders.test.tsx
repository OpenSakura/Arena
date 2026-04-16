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

function Probe() {
  const { headers, authStatus } = useAuthHeaders();

  return (
    <div>
      <span>{authStatus}</span>
      <span>{headers?.Authorization ?? "no-auth"}</span>
    </div>
  );
}

describe("useAuthHeaders", () => {
  beforeEach(() => {
    useArenaAuthMock.mockReset();
  });

  it("updates headers immediately when auth becomes authenticated", () => {
    useArenaAuthMock
      .mockReturnValueOnce(makeAuthState({ authStatus: "loading", isLoading: true }))
      .mockReturnValue(
        makeAuthState({
          authStatus: "authenticated",
          isAuthenticated: true,
          accessToken: "admin-token",
          headers: { Authorization: "Bearer admin-token" },
          headersRef: { current: { Authorization: "Bearer admin-token" } },
          accessTokenRef: { current: "admin-token" },
        }),
      );

    const { rerender } = render(<Probe />);
    expect(screen.getByText("no-auth")).toBeDefined();

    rerender(<Probe />);

    expect(screen.getByText("Bearer admin-token")).toBeDefined();
  });

  it("keeps auth headers unset while unauthenticated", async () => {
    useArenaAuthMock.mockReturnValue(makeAuthState());

    render(<Probe />);

    await waitFor(() => {
      expect(screen.getByText("unauthenticated")).toBeDefined();
    });
    expect(screen.getByText("no-auth")).toBeDefined();
  });
});
