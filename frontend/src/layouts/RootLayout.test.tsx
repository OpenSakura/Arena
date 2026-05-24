import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
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

    render(<RouterProvider router={memRouter} future={{ v7_startTransition: true }} />);

    expect(await screen.findByRole("banner")).toBeDefined();
    expect(await screen.findByRole("contentinfo")).toBeDefined();
    
    const main = screen.getByRole("main");
    expect(main.className).toContain("max-w-7xl");
    expect(main.className).toContain("mx-auto");
  });
});
