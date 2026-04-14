import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { useAdminAccess } from "./useAdminAccess";

const useSessionMock = vi.fn();
const apiGetMock = vi.fn();

vi.mock("next-auth/react", () => ({
  useSession: () => useSessionMock(),
}));

vi.mock("@/lib/api", () => ({
  apiGet: (...args: unknown[]) => apiGetMock(...args),
}));

beforeEach(() => {
  useSessionMock.mockReset();
  apiGetMock.mockReset();
});

describe("useAdminAccess", () => {
  it("returns loading state while session is loading", () => {
    useSessionMock.mockReturnValue({ data: null, status: "loading" });

    const { result } = renderHook(() => useAdminAccess());

    expect(result.current.loading).toBe(true);
    expect(result.current.isAuthenticated).toBe(false);
    expect(result.current.isAdmin).toBe(null);
  });

  it("returns unauthenticated state when session is unauthenticated", () => {
    useSessionMock.mockReturnValue({ data: null, status: "unauthenticated" });

    const { result } = renderHook(() => useAdminAccess());

    expect(result.current.loading).toBe(false);
    expect(result.current.isAuthenticated).toBe(false);
    expect(result.current.isAdmin).toBe(false);
  });

  it("returns is_admin: false when API returns false", async () => {
    useSessionMock.mockReturnValue({
      data: { accessToken: "tok-false", user: {}, expires: "2099" },
      status: "authenticated",
    });
    apiGetMock.mockResolvedValue({ authenticated: true, is_admin: false });

    const { result } = renderHook(() => useAdminAccess());

    expect(result.current.loading).toBe(true);

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
      expect(result.current.isAdmin).toBe(false);
    });
    expect(result.current.isAuthenticated).toBe(true);
  });

  it("returns is_admin: true when API returns true", async () => {
    useSessionMock.mockReturnValue({
      data: { accessToken: "tok-true", user: {}, expires: "2099" },
      status: "authenticated",
    });
    apiGetMock.mockResolvedValue({ authenticated: true, is_admin: true });

    const { result } = renderHook(() => useAdminAccess());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
      expect(result.current.isAdmin).toBe(true);
    });
    expect(result.current.isAuthenticated).toBe(true);
  });

  it("returns is_admin: false when API fails", async () => {
    useSessionMock.mockReturnValue({
      data: { accessToken: "tok-fail", user: {}, expires: "2099" },
      status: "authenticated",
    });
    apiGetMock.mockRejectedValue(new Error("network error"));

    const { result } = renderHook(() => useAdminAccess());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
      expect(result.current.isAdmin).toBe(false);
    });
  });

  it("stops before /me probing when the session refresh has failed", async () => {
    useSessionMock.mockReturnValue({
      data: {
        accessToken: "tok-expired",
        error: "RefreshTokenExpired",
        user: {},
        expires: "2099",
      },
      status: "authenticated",
    });

    const { result } = renderHook(() => useAdminAccess());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.isAuthenticated).toBe(true);
    expect(result.current.isAdmin).toBe(false);
    expect(result.current.error).toBe("Your session has expired. Please log in again.");
    expect(apiGetMock).not.toHaveBeenCalled();
  });
});
