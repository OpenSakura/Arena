import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { createMemoryRouter, RouterProvider } from "react-router-dom";

import { createTestI18n, TestI18nProvider } from "@/i18n/test-utils";
import { RootLayout } from "./RootLayout";
import { routerFutureConfig } from "@/router";

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

describe("RootLayout", () => {
  it("renders header, main wrapper with correct spacing, and footer", async () => {
    const i18n = await createTestI18n("en");
    const memRouter = createMemoryRouter(
      [
        {
          path: "/",
          element: <RootLayout />,
          children: [{ index: true, element: <div>Root Content</div> }],
        },
      ],
      { initialEntries: ["/"], future: routerFutureConfig },
    );

    render(
      <TestI18nProvider i18n={i18n}>
        <RouterProvider router={memRouter} future={{ v7_startTransition: true }} />
      </TestI18nProvider>,
    );

    expect(await screen.findByRole("banner")).toBeDefined();
    expect(await screen.findByRole("contentinfo")).toBeDefined();
    
    const main = screen.getByRole("main");
    expect(main.className).toContain("max-w-7xl");
    expect(main.className).toContain("mx-auto");
  });

  it("renders wider main wrapper for /battle routes", async () => {
    const i18n = await createTestI18n("en");
    const memRouter = createMemoryRouter(
      [
        {
          path: "/",
          element: <RootLayout />,
          children: [{ path: "battle/new", element: <div>Battle Content</div> }],
        },
      ],
      { initialEntries: ["/battle/new"], future: routerFutureConfig },
    );

    render(
      <TestI18nProvider i18n={i18n}>
        <RouterProvider router={memRouter} future={{ v7_startTransition: true }} />
      </TestI18nProvider>,
    );
    
    const main = screen.getByRole("main");
    expect(main.className).toContain("max-w-none");
    expect(main.className).toContain("lg:max-w-[80vw]");
    expect(main.className).not.toContain("max-w-7xl");
    expect(main.className).toContain("mx-auto");
  });
});
