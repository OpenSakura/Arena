import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import AdminIndexPage from "./page";
import AdminLayout from "./layout";

const usePathnameMock = vi.fn();
const useRouterMock = vi.fn();
const useSessionMock = vi.fn();

vi.mock("next/navigation", () => ({
  usePathname: () => usePathnameMock(),
  useRouter: () => useRouterMock(),
}));

vi.mock("next-auth/react", () => ({
  useSession: () => useSessionMock(),
}));

beforeEach(() => {
  usePathnameMock.mockReset();
  useRouterMock.mockReset();
  useSessionMock.mockReset();
});

describe("AdminIndexPage", () => {
  it("replaces /admin with /admin/models", async () => {
    const replaceMock = vi.fn();
    useRouterMock.mockReturnValue({ replace: replaceMock });

    render(<AdminIndexPage />);

    await waitFor(() => {
      expect(replaceMock).toHaveBeenCalledWith("/admin/models");
    });
  });

  it("admin layout renders children on /admin so redirect page can mount", () => {
    usePathnameMock.mockReturnValue("/admin");
    useSessionMock.mockReturnValue({ data: null, status: "unauthenticated" });

    render(
      <AdminLayout>
        <div>redirect-child</div>
      </AdminLayout>,
    );

    expect(screen.getByText("redirect-child")).toBeDefined();
    expect(
      screen.queryByText("You must be logged in with an admin account to access this area."),
    ).toBeNull();
  });

  it("admin layout also treats /admin/ as the redirect entry route", () => {
    usePathnameMock.mockReturnValue("/admin/");
    useSessionMock.mockReturnValue({ data: null, status: "unauthenticated" });

    render(
      <AdminLayout>
        <div>redirect-child</div>
      </AdminLayout>,
    );

    expect(screen.getByText("redirect-child")).toBeDefined();
    expect(
      screen.queryByText("You must be logged in with an admin account to access this area."),
    ).toBeNull();
  });
});
