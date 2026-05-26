import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { createTestI18n, TestI18nProvider } from "@/i18n/test-utils";
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

async function renderAdminLayout(initialEntry = "/admin/models", locale: "en" | "zh" = "en") {
  const i18n = await createTestI18n(locale);

  return render(
    <TestI18nProvider i18n={i18n}>
      <MemoryRouter initialEntries={[initialEntry]} future={routerFutureConfig}>
        <Routes>
          <Route path="/" element={<div data-testid="home-destination">Home destination</div>} />
          <Route path="/admin" element={<AdminLayout />}>
            <Route path="models" element={<div data-testid="admin-outlet">Admin Content</div>} />
            <Route path="tasks" element={<div data-testid="admin-outlet">Admin Tasks</div>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </TestI18nProvider>,
  );
}

beforeEach(() => {
  useAdminAccessMock.mockReset();
  navigateSpy.mockReset();
  useAdminAccessMock.mockReturnValue(makeAdminAccessState());
});

describe("AdminLayout", () => {
  it("renders English admin tabs while preserving admin routes", async () => {
    await renderAdminLayout();

    expect(screen.getByTestId("admin-outlet")).toBeDefined();
    expect(screen.getByRole("heading", { name: "Admin" })).toBeDefined();
    expect(screen.getByRole("link", { name: "Models" }).getAttribute("href")).toBe("/admin/models");
    expect(screen.getByRole("link", { name: "Tasks" }).getAttribute("href")).toBe("/admin/tasks");
    expect(screen.getByRole("link", { name: "Service Accounts" }).getAttribute("href")).toBe("/admin/service-accounts");
  });

  it("renders Chinese admin tabs while preserving admin routes", async () => {
    await renderAdminLayout("/admin/tasks", "zh");

    expect(screen.getByTestId("admin-outlet")).toBeDefined();
    expect(screen.getByRole("heading", { name: "管理" })).toBeDefined();
    expect(screen.getByRole("link", { name: "模型" }).getAttribute("href")).toBe("/admin/models");
    expect(screen.getByRole("link", { name: "任务" }).getAttribute("href")).toBe("/admin/tasks");
    expect(screen.getByRole("link", { name: "服务账号" }).getAttribute("href")).toBe("/admin/service-accounts");
  });

  it("keeps protected content hidden while auth is still loading", async () => {
    useAdminAccessMock.mockReturnValue(makeAdminAccessState({ loading: true, isAuthenticated: false, isAdmin: false }));

    const { container } = await renderAdminLayout();

    expect(screen.queryByTestId("admin-outlet")).toBeNull();
    expect(container.querySelector(".shimmer")).toBeDefined();
  });

  it("redirects unauthenticated users home with the attempted callback path", async () => {
    useAdminAccessMock.mockReturnValue(makeAdminAccessState({ isAuthenticated: false, isAdmin: false }));

    await renderAdminLayout("/admin/models?view=all#danger-zone");

    expect(screen.getByTestId("navigate-intent")).toBeDefined();
    expect(navigateSpy).toHaveBeenCalledWith({
      to: "/?callbackUrl=%2Fadmin%2Fmodels%3Fview%3Dall%23danger-zone",
      replace: true,
    });
    expect(screen.queryByTestId("admin-outlet")).toBeNull();
  });

  it("shows the English not-authorized message in place for authenticated non-admin users", async () => {
    useAdminAccessMock.mockReturnValue(makeAdminAccessState({ isAdmin: false }));

    await renderAdminLayout();

    expect(screen.getByText("You are not authorized to access the admin area.")).toBeDefined();
    expect(screen.queryByTestId("admin-outlet")).toBeNull();
  });

  it("shows the Chinese not-authorized message in place for authenticated non-admin users", async () => {
    useAdminAccessMock.mockReturnValue(makeAdminAccessState({ isAdmin: false }));

    await renderAdminLayout("/admin/models", "zh");

    expect(screen.getByText("你没有访问管理后台的权限。")).toBeDefined();
    expect(screen.queryByTestId("admin-outlet")).toBeNull();
  });

  it("localizes the session-expired guard message without redirecting away", async () => {
    useAdminAccessMock.mockReturnValue(
      makeAdminAccessState({
        isAuthenticated: true,
        isAdmin: false,
        error: "Your session has expired. Please log in again.",
      }),
    );

    await renderAdminLayout("/admin/models", "zh");

    expect(screen.getByText("登录已过期，请重新登录。")).toBeDefined();
    expect(navigateSpy).not.toHaveBeenCalled();
    expect(screen.queryByTestId("admin-outlet")).toBeNull();
  });
});
