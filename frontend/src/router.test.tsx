import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { createTestI18n, TestI18nProvider } from "@/i18n/test-utils";
import { router, routerFutureConfig } from "./router";

vi.mock("@/hooks/useArenaAuth", () => ({
  useArenaAuth: () => ({
    authStatus: "unauthenticated",
    isLoading: false,
    isAuthenticated: false,
    user: null,
    sessionError: null,
    signinRedirect: vi.fn(),
    signoutRedirect: vi.fn(),
  }),
}));

vi.mock("@/components/BattleView", () => ({
  BattleView: ({ battleId }: { battleId: string }) => <div>Battle route: {battleId}</div>,
}));

async function renderRouterAt(initialEntry: string, locale: "en" | "zh" = "en") {
  const i18n = await createTestI18n(locale);
  const memRouter = createMemoryRouter(router.routes, { initialEntries: [initialEntry], future: routerFutureConfig });

  return render(
    <TestI18nProvider i18n={i18n}>
      <RouterProvider router={memRouter} future={{ v7_startTransition: true }} />
    </TestI18nProvider>,
  );
}

describe("router", () => {
  const originalTitle = document.title;

  afterEach(() => {
    document.title = originalTitle;
  });

  it("renders home", async () => {
    await renderRouterAt("/");
    expect(await screen.findByRole("heading", { name: /Open\s*Sakura\s*Arena/i })).toBeDefined();
  });

  it("contains required routes", async () => {
    await renderRouterAt("/battle/new");
    expect(await screen.findByText("Battle route: new")).toBeDefined();
  });

  it("defines stable localized title handles without changing route paths", () => {
    const rootRoute = router.routes[0];
    const children = rootRoute.children ?? [];
    const byPath = new Map(children.map((route) => [route.path, route]));
    const homeRoute = children.find((route) => route.index);
    const adminRoute = byPath.get("admin");
    const adminChildren = adminRoute?.children ?? [];
    const adminByPath = new Map(adminChildren.map((route) => [route.path, route]));
    const adminIndexRoute = adminChildren.find((route) => route.index);

    expect(rootRoute.path).toBe("/");
    expect(rootRoute.handle).toEqual({ titleKey: "routes.home" });
    expect(homeRoute?.handle).toEqual({ titleKey: "routes.home" });
    expect(byPath.get("battle/:battleId")?.handle).toEqual({ titleKey: "routes.battle" });
    expect(byPath.get("leaderboard")?.handle).toEqual({ titleKey: "routes.leaderboard" });
    expect(byPath.get("onboarding")?.handle).toEqual({ titleKey: "routes.onboarding" });
    expect(byPath.get("auth/error")?.handle).toEqual({ titleKey: "routes.authError" });
    expect(adminRoute?.handle).toEqual({ titleKey: "routes.admin" });
    expect(adminIndexRoute?.handle).toEqual({ titleKey: "routes.adminModels" });
    expect(adminByPath.get("models")?.handle).toEqual({ titleKey: "routes.adminModels" });
    expect(adminByPath.get("tasks")?.handle).toEqual({ titleKey: "routes.adminTasks" });
    expect(adminByPath.get("service-accounts")?.handle).toEqual({ titleKey: "routes.adminServiceAccounts" });
  });

  it("uses route title handles to localize document title", async () => {
    await renderRouterAt("/auth/error", "zh");

    await waitFor(() => {
      expect(document.title).toBe("认证错误 | OpenSakura Arena");
    });
  });

  it("has no frontend auth callback route", async () => {
    const authRoute = router.routes[0].children?.find(r => r.path === "auth/callback");
    expect(authRoute).toBeUndefined();
  });

  it("has no frontend silent callback route", async () => {
    const authRoute = router.routes[0].children?.find(r => r.path === "auth/silent-callback");
    expect(authRoute).toBeUndefined();
  });

  it("has no frontend logout callback route", async () => {
    const authRoute = router.routes[0].children?.find(r => r.path === "auth/logout-callback");
    expect(authRoute).toBeUndefined();
  });

  it("renders a simple backend auth error route", async () => {
    await renderRouterAt("/auth/error?message=Login%20failed");

    expect(await screen.findByRole("heading", { name: "Authentication error" })).toBeDefined();
    expect(screen.getByText("Login failed")).toBeDefined();
  });

  it("localizes backend auth error fallback copy", async () => {
    await renderRouterAt("/auth/error", "zh");

    expect(await screen.findByRole("heading", { name: "认证错误" })).toBeDefined();
    expect(screen.getByText("认证无法完成，请重试。")).toBeDefined();
  });

  it("redirects /admin to /admin/models", () => {
    const adminRoute = router.routes[0].children?.find(r => r.path === "admin");
    const indexRoute = adminRoute?.children?.find(r => r.index);
    expect(indexRoute?.element?.props.to).toBe("/admin/models");
  });
});
