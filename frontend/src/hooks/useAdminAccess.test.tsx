import { renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useAdminAccess } from "./useAdminAccess";

const useAuthHeadersMock = vi.fn();
const SESSION_EXPIRED_MESSAGE = "Your session has expired. Please log in again.";

vi.mock("@/hooks/useAuthHeaders", () => ({
  useAuthHeaders: () => useAuthHeadersMock(),
}));

function makeAuthHeadersState(overrides: Record<string, unknown> = {}) {
  return {
    authStatus: "unauthenticated",
    csrfToken: null,
    sessionError: null,
    user: null,
    ...overrides,
  };
}

function authenticatedHeaders(overrides: Record<string, unknown> = {}) {
  return makeAuthHeadersState({
    authStatus: "authenticated",
    csrfToken: "csrf-token",
    ...overrides,
  });
}

beforeEach(() => {
  useAuthHeadersMock.mockReset();

  useAuthHeadersMock.mockReturnValue(makeAuthHeadersState());
});

describe("useAdminAccess", () => {
  it("returns loading state while auth headers are still loading", () => {
    useAuthHeadersMock.mockReturnValue(makeAuthHeadersState({ authStatus: "loading" }));

    const { result } = renderHook(() => useAdminAccess());

    expect(result.current.loading).toBe(true);
    expect(result.current.isAuthenticated).toBe(false);
    expect(result.current.isAdmin).toBe(false);
    expect(result.current.error).toBeNull();
  });

  it("returns unauthenticated state when auth status is unauthenticated", () => {
    const { result } = renderHook(() => useAdminAccess());

    expect(result.current.loading).toBe(false);
    expect(result.current.isAuthenticated).toBe(false);
    expect(result.current.isAdmin).toBe(false);
    expect(result.current.error).toBeNull();
  });

  it("returns is_admin: false when backend session user is not admin", () => {
    useAuthHeadersMock.mockReturnValue(authenticatedHeaders({ user: { isAdmin: false } }));

    const { result } = renderHook(() => useAdminAccess());

    expect(result.current.loading).toBe(false);
    expect(result.current.isAdmin).toBe(false);
    expect(result.current.isAuthenticated).toBe(true);
  });

  it("returns is_admin: true when backend session user is admin", () => {
    useAuthHeadersMock.mockReturnValue(authenticatedHeaders({ user: { isAdmin: true } }));

    const { result } = renderHook(() => useAdminAccess());

    expect(result.current.loading).toBe(false);
    expect(result.current.isAdmin).toBe(true);
    expect(result.current.isAuthenticated).toBe(true);
  });

  it("returns the expired-session message when session bootstrap has failed", () => {
    useAuthHeadersMock.mockReturnValue(
      authenticatedHeaders({ sessionError: "SessionBootstrapFailed" }),
    );

    const { result } = renderHook(() => useAdminAccess());

    expect(result.current.loading).toBe(false);
    expect(result.current.isAuthenticated).toBe(true);
    expect(result.current.isAdmin).toBe(false);
    expect(result.current.error).toBe(SESSION_EXPIRED_MESSAGE);
  });
});
