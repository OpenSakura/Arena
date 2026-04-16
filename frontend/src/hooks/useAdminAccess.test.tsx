import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useAdminAccess } from "./useAdminAccess";

const useAuthHeadersMock = vi.fn();
const apiGetMock = vi.fn();
const SESSION_EXPIRED_MESSAGE = "Your session has expired. Please log in again.";

vi.mock("@/hooks/useAuthHeaders", () => ({
  useAuthHeaders: () => useAuthHeadersMock(),
}));

vi.mock("@/lib/api", () => ({
  apiGet: (...args: unknown[]) => apiGetMock(...args),
}));

function makeAuthHeadersState(overrides: Record<string, unknown> = {}) {
  return {
    authStatus: "unauthenticated",
    accessToken: null,
    headers: undefined,
    sessionError: null,
    headersRef: { current: undefined },
    accessTokenRef: { current: null },
    ...overrides,
  };
}

beforeEach(() => {
  useAuthHeadersMock.mockReset();
  apiGetMock.mockReset();

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

  it("returns unauthenticated state when auth status is unauthenticated", async () => {
    const { result } = renderHook(() => useAdminAccess());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.isAuthenticated).toBe(false);
    expect(result.current.isAdmin).toBe(false);
    expect(result.current.error).toBeNull();
  });

  it("returns is_admin: false when API returns false", async () => {
    useAuthHeadersMock.mockReturnValue(
      makeAuthHeadersState({
        authStatus: "authenticated",
        accessToken: "tok-false",
        headers: { Authorization: "Bearer tok-false" },
        headersRef: { current: { Authorization: "Bearer tok-false" } },
        accessTokenRef: { current: "tok-false" },
      }),
    );
    apiGetMock.mockResolvedValue({ authenticated: true, is_admin: false });

    const { result } = renderHook(() => useAdminAccess());

    expect(result.current.loading).toBe(true);

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
      expect(result.current.isAdmin).toBe(false);
    });

    expect(result.current.isAuthenticated).toBe(true);
    expect(apiGetMock).toHaveBeenCalledWith("/me", {
      headers: { Authorization: "Bearer tok-false" },
    });
  });

  it("treats an unauthenticated /me response as an expired session", async () => {
    useAuthHeadersMock.mockReturnValue(
      makeAuthHeadersState({
        authStatus: "authenticated",
        accessToken: "tok-stale",
        headers: { Authorization: "Bearer tok-stale" },
        headersRef: { current: { Authorization: "Bearer tok-stale" } },
        accessTokenRef: { current: "tok-stale" },
      }),
    );
    apiGetMock.mockResolvedValue({ authenticated: false, is_admin: false });

    const { result } = renderHook(() => useAdminAccess());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.isAuthenticated).toBe(true);
    expect(result.current.isAdmin).toBe(false);
    expect(result.current.error).toBe(SESSION_EXPIRED_MESSAGE);
  });

  it("returns is_admin: true when API returns true", async () => {
    useAuthHeadersMock.mockReturnValue(
      makeAuthHeadersState({
        authStatus: "authenticated",
        accessToken: "tok-true",
        headers: { Authorization: "Bearer tok-true" },
        headersRef: { current: { Authorization: "Bearer tok-true" } },
        accessTokenRef: { current: "tok-true" },
      }),
    );
    apiGetMock.mockResolvedValue({ authenticated: true, is_admin: true });

    const { result } = renderHook(() => useAdminAccess());

    expect(result.current.loading).toBe(true);

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
      expect(result.current.isAdmin).toBe(true);
    });

    expect(result.current.isAuthenticated).toBe(true);
  });

  it("returns is_admin: false when API fails", async () => {
    useAuthHeadersMock.mockReturnValue(
      makeAuthHeadersState({
        authStatus: "authenticated",
        accessToken: "tok-fail",
        headers: { Authorization: "Bearer tok-fail" },
        headersRef: { current: { Authorization: "Bearer tok-fail" } },
        accessTokenRef: { current: "tok-fail" },
      }),
    );
    apiGetMock.mockRejectedValue(new Error("network error"));

    const { result } = renderHook(() => useAdminAccess());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
      expect(result.current.isAdmin).toBe(false);
    });

    expect(result.current.error).toBe("network error");
  });

  it("maps 401 /me failures to the expired-session message", async () => {
    useAuthHeadersMock.mockReturnValue(
      makeAuthHeadersState({
        authStatus: "authenticated",
        accessToken: "tok-401",
        headers: { Authorization: "Bearer tok-401" },
        headersRef: { current: { Authorization: "Bearer tok-401" } },
        accessTokenRef: { current: "tok-401" },
      }),
    );
    apiGetMock.mockRejectedValue(new Error("GET /me failed: 401 - Authentication required"));

    const { result } = renderHook(() => useAdminAccess());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.isAuthenticated).toBe(true);
    expect(result.current.isAdmin).toBe(false);
    expect(result.current.error).toBe(SESSION_EXPIRED_MESSAGE);
  });

  it("stops before /me probing when session refresh has failed", async () => {
    useAuthHeadersMock.mockReturnValue(
      makeAuthHeadersState({
        authStatus: "authenticated",
        accessToken: "tok-expired",
        headers: { Authorization: "Bearer tok-expired" },
        sessionError: "RefreshTokenExpired",
        headersRef: { current: { Authorization: "Bearer tok-expired" } },
        accessTokenRef: { current: "tok-expired" },
      }),
    );

    const { result } = renderHook(() => useAdminAccess());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.isAuthenticated).toBe(true);
    expect(result.current.isAdmin).toBe(false);
    expect(result.current.error).toBe(SESSION_EXPIRED_MESSAGE);
    expect(apiGetMock).not.toHaveBeenCalled();
  });
});
