import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { routerFutureConfig } from "@/router";

import { AdminLayout } from "./AdminLayout";

const useAdminAccessMock = vi.fn();
const navigateSpy = vi.fn();

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return {
    ...actual,
    Navigate: (props: { to: string; replace?: boolean }) => {
      navigateSpy(props);
      return <div data-testid="navigate-intent" />;
    },
  };
});

vi.mock("@/hooks/useAdminAccess", () => ({
  useAdminAccess: () => useAdminAccessMock(),
}));

function makeAdminAccessState(overrides: Record<string, unknown> = {}) {
  return {
    isAuthenticated: true,
    isAdmin: true,
    loading: false,
    error: null,
    ...overrides,
  };
}

function renderAdminLayout(initialEntry = "/admin/models") {
  return render(
    <MemoryRouter initialEntries={[initialEntry]} future={routerFutureConfig}>
      <Routes>
        <Route path="/" element={<div data-testid="home-destination">Home destination</div>} />
        <Route path="/admin" element={<AdminLayout />}>
          <Route path="models" element={<div data-testid="admin-outlet">Admin Content</div>} />
          <Route path="tasks" element={<div data-testid="admin-outlet">Admin Tasks</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  useAdminAccessMock.mockReset();
  navigateSpy.mockReset();
  useAdminAccessMock.mockReturnValue(makeAdminAccessState());
});

describe("AdminLayout", () => {
  it("renders the admin shell and outlet for admin users", () => {
    renderAdminLayout();

    expect(screen.getByTestId("admin-outlet")).toBeDefined();
    expect(screen.getByText("Admin")).toBeDefined();
    expect(screen.getByRole("link", { name: "Models" }).getAttribute("href")).toBe("/admin/models");
    expect(screen.getByRole("link", { name: "Tasks" }).getAttribute("href")).toBe("/admin/tasks");
  });

  it("keeps protected content hidden while auth is still loading", () => {
    useAdminAccessMock.mockReturnValue(makeAdminAccessState({ loading: true, isAuthenticated: false, isAdmin: false }));

    const { container } = renderAdminLayout();

    expect(screen.queryByTestId("admin-outlet")).toBeNull();
    expect(container.querySelector(".shimmer")).toBeDefined();
  });

  it("redirects unauthenticated users home with the attempted callback path", () => {
    useAdminAccessMock.mockReturnValue(makeAdminAccessState({ isAuthenticated: false, isAdmin: false }));

    renderAdminLayout("/admin/models?view=all#danger-zone");

    expect(screen.getByTestId("navigate-intent")).toBeDefined();
    expect(navigateSpy).toHaveBeenCalledWith({
      to: "/?callbackUrl=%2Fadmin%2Fmodels%3Fview%3Dall%23danger-zone",
      replace: true,
    });
    expect(screen.queryByTestId("admin-outlet")).toBeNull();
  });

  it("shows the not-authorized message in place for authenticated non-admin users", () => {
    useAdminAccessMock.mockReturnValue(makeAdminAccessState({ isAdmin: false }));

    renderAdminLayout();

    expect(screen.getByText("You are not authorized to access the admin area.")).toBeDefined();
    expect(screen.queryByTestId("admin-outlet")).toBeNull();
  });

  it("shows the current session-expired message without redirecting away", () => {
    useAdminAccessMock.mockReturnValue(
      makeAdminAccessState({
        isAuthenticated: true,
        isAdmin: false,
        error: "Your session has expired. Please log in again.",
      }),
    );

    renderAdminLayout();

    expect(screen.getByText("Your session has expired. Please log in again.")).toBeDefined();
    expect(navigateSpy).not.toHaveBeenCalled();
    expect(screen.queryByTestId("admin-outlet")).toBeNull();
  });
});
