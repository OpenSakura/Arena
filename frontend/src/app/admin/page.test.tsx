import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";

import AdminIndexPage from "./page";
import AdminLayout from "./layout";

const usePathnameMock = vi.fn();
const redirectMock = vi.fn();
const useSessionMock = vi.fn();
const apiGetMock = vi.fn();

vi.mock("next/navigation", () => ({
  usePathname: () => usePathnameMock(),
  redirect: (...args: unknown[]) => redirectMock(...args),
}));

vi.mock("next-auth/react", () => ({
  useSession: () => useSessionMock(),
}));

vi.mock("@/lib/api", () => ({
  apiGet: (...args: unknown[]) => apiGetMock(...args),
}));

beforeEach(() => {
  usePathnameMock.mockReset();
  redirectMock.mockReset();
  useSessionMock.mockReset();
  apiGetMock.mockReset();
});

describe("AdminIndexPage", () => {
  it("redirects /admin to /admin/models on the server", () => {
    AdminIndexPage();
    expect(redirectMock).toHaveBeenCalledWith("/admin/models");
  });
});

describe("AdminLayout", () => {
  it("shows loading shimmer while session is loading", () => {
    usePathnameMock.mockReturnValue("/admin/models");
    useSessionMock.mockReturnValue({ data: null, status: "loading" });

    const { container } = render(
      <AdminLayout>
        <div>child-content</div>
      </AdminLayout>,
    );

    expect(screen.queryByText("child-content")).toBeNull();
    expect(container.querySelector(".shimmer")).toBeDefined();
  });

  it("shows not-logged-in message for unauthenticated users", () => {
    usePathnameMock.mockReturnValue("/admin/models");
    useSessionMock.mockReturnValue({ data: null, status: "unauthenticated" });

    render(
      <AdminLayout>
        <div>child-content</div>
      </AdminLayout>,
    );

    expect(
      screen.getByText("You must be logged in with an admin account to access this area."),
    ).toBeDefined();
    expect(screen.queryByText("child-content")).toBeNull();
  });

  it("shows not-authorized message when /me returns is_admin: false", async () => {
    usePathnameMock.mockReturnValue("/admin/models");
    useSessionMock.mockReturnValue({
      data: { accessToken: "tok-false", user: {}, expires: "2099" },
      status: "authenticated",
    });
    apiGetMock.mockResolvedValue({ authenticated: true, is_admin: false });

    render(
      <AdminLayout>
        <div>child-content</div>
      </AdminLayout>,
    );

    await waitFor(() => {
      expect(
        screen.getByText("You are not authorized to access the admin area."),
      ).toBeDefined();
    });
    expect(screen.queryByText("child-content")).toBeNull();
  });

  it("renders admin shell and children when /me returns is_admin: true", async () => {
    usePathnameMock.mockReturnValue("/admin/models");
    useSessionMock.mockReturnValue({
      data: { accessToken: "tok-true", user: {}, expires: "2099" },
      status: "authenticated",
    });
    apiGetMock.mockResolvedValue({ authenticated: true, is_admin: true });

    render(
      <AdminLayout>
        <div>child-content</div>
      </AdminLayout>,
    );

    await waitFor(() => {
      expect(screen.getByText("child-content")).toBeDefined();
    });
    expect(screen.getByText("Admin")).toBeDefined();
  });

  it("shows not-authorized message when /me call fails", async () => {
    usePathnameMock.mockReturnValue("/admin/models");
    useSessionMock.mockReturnValue({
      data: { accessToken: "tok-fail", user: {}, expires: "2099" },
      status: "authenticated",
    });
    apiGetMock.mockRejectedValue(new Error("network error"));

    render(
      <AdminLayout>
        <div>child-content</div>
      </AdminLayout>,
    );

    await waitFor(() => {
      expect(
        screen.getByText("network error"),
      ).toBeDefined();
    });
  });

  it("shows expired-session messaging without probing /me", async () => {
    usePathnameMock.mockReturnValue("/admin/models");
    useSessionMock.mockReturnValue({
      data: {
        accessToken: "tok-expired",
        error: "RefreshTokenExpired",
        user: {},
        expires: "2099",
      },
      status: "authenticated",
    });

    render(
      <AdminLayout>
        <div>child-content</div>
      </AdminLayout>,
    );

    await waitFor(() => {
      expect(
        screen.getByText("Your session has expired. Please log in again."),
      ).toBeDefined();
    });
    expect(apiGetMock).not.toHaveBeenCalled();
    expect(screen.queryByText("child-content")).toBeNull();
  });
});
