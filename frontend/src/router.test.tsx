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
    accessToken: null,
    sessionError: null,
    headers: undefined,
    headersRef: { current: undefined },
    accessTokenRef: { current: null },
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

  it("registers the signin callback route", async () => {
    const memRouter = createMemoryRouter(router.routes, {
      initialEntries: ["/auth/callback?code=abc&state=def"],
      future: routerFutureConfig,
    });
    render(<RouterProvider router={memRouter} future={{ v7_startTransition: true }} />);
    expect(await screen.findByText("Auth Callback")).toBeDefined();
  });

  it("registers the silent renew callback route", async () => {
    const memRouter = createMemoryRouter(router.routes, {
      initialEntries: ["/auth/silent-callback?state=silent-renew"],
      future: routerFutureConfig,
    });
    render(<RouterProvider router={memRouter} future={{ v7_startTransition: true }} />);
    expect(await screen.findByText("Auth Silent Callback")).toBeDefined();
  });

  it("registers the logout callback route", async () => {
    const memRouter = createMemoryRouter(router.routes, {
      initialEntries: ["/auth/logout-callback?state=logout"],
      future: routerFutureConfig,
    });
    render(<RouterProvider router={memRouter} future={{ v7_startTransition: true }} />);
    expect(await screen.findByText("Auth Logout Callback")).toBeDefined();
  });

  it("redirects /admin to /admin/models", () => {
    const adminRoute = router.routes[0].children?.find(r => r.path === "admin");
    const indexRoute = adminRoute?.children?.find(r => r.index);
    expect(indexRoute?.element?.props.to).toBe("/admin/models");
  });
});
