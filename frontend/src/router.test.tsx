import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { createMemoryRouter, RouterProvider } from "react-router-dom";
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

describe("router", () => {
  it("renders home", async () => {
    const memRouter = createMemoryRouter(router.routes, { initialEntries: ["/"], future: routerFutureConfig });
    render(<RouterProvider router={memRouter} future={{ v7_startTransition: true }} />);
    expect(await screen.findByRole("heading", { name: /Open\s*Sakura\s*Arena/i })).toBeDefined();
  });

  it("contains required routes", async () => {
    const memRouter = createMemoryRouter(router.routes, { initialEntries: ["/battle/new"], future: routerFutureConfig });
    render(<RouterProvider router={memRouter} future={{ v7_startTransition: true }} />);
    expect(await screen.findByText("Battle route: new")).toBeDefined();
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
    const memRouter = createMemoryRouter(router.routes, {
      initialEntries: ["/auth/error?message=Login%20failed"],
      future: routerFutureConfig,
    });
    render(<RouterProvider router={memRouter} future={{ v7_startTransition: true }} />);
    expect(await screen.findByRole("heading", { name: "Authentication error" })).toBeDefined();
    expect(screen.getByText("Login failed")).toBeDefined();
  });

  it("redirects /admin to /admin/models", () => {
    const adminRoute = router.routes[0].children?.find(r => r.path === "admin");
    const indexRoute = adminRoute?.children?.find(r => r.index);
    expect(indexRoute?.element?.props.to).toBe("/admin/models");
  });
});
