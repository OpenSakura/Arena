// @vitest-environment jsdom

import { render, screen } from "@testing-library/react";
import type { AnchorHTMLAttributes, ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import LeaderboardPage from "./page";
import type { LeaderboardSearchParams } from "@/lib/leaderboard";

const apiGetMock = vi.fn();

type MockLinkProps = {
  href: string;
  children: ReactNode;
} & Omit<AnchorHTMLAttributes<HTMLAnchorElement>, "href">;

vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: MockLinkProps) => {
    return (
      <a href={href} {...props}>
        {children}
      </a>
    );
  },
}));

vi.mock("@/lib/api", () => ({
  apiGet: (...args: unknown[]) => apiGetMock(...args),
}));

beforeEach(() => {
  apiGetMock.mockReset();
});

describe("LeaderboardPage", () => {
  it("requests default Elo leaderboard and shows empty state", async () => {
    apiGetMock.mockResolvedValue({
      method: "elo",
      ci: false,
      bootstrap_rounds: null,
      models: [],
    });

    const element = await LeaderboardPage({ searchParams: Promise.resolve({}) as Promise<LeaderboardSearchParams> });
    render(element);

    expect(apiGetMock).toHaveBeenCalledWith("/leaderboard?method=elo");
    expect(screen.getByText("ELO")).toBeDefined();
    expect(screen.getByText("No ratings yet")).toBeDefined();
    expect(screen.getByText("95% CI")).toBeDefined();
    expect(screen.getByRole("link", { name: "Elo (baseline)" }).getAttribute("href")).toBe(
      "/leaderboard?method=elo",
    );
    expect(screen.getByRole("link", { name: "Show 95% CI" }).getAttribute("href")).toBe(
      "/leaderboard?method=elo&include_confidence=true",
    );
    expect(screen.queryByRole("columnheader", { name: "95% CI" })).toBeNull();
  });

  it("renders BT rows with confidence intervals when requested", async () => {
    apiGetMock.mockResolvedValue({
      method: "bt",
      ci: true,
      bootstrap_rounds: 250,
      models: [
        {
          model_id: "model-a",
          display_name: "Model A",
          rating: 1210.36,
          rating_lower: 1188.9,
          rating_upper: 1234.2,
          games_played: 42,
        },
      ],
    });

    const element = await LeaderboardPage({
      searchParams: Promise.resolve({ method: "bt", include_confidence: "true" }),
    });
    render(element);

    expect(apiGetMock).toHaveBeenCalledWith("/leaderboard?method=bt&include_confidence=true");
    expect(screen.getByText("BT")).toBeDefined();
    expect(screen.getByText(/250 bootstrap rounds/)).toBeDefined();
    expect(screen.getByRole("link", { name: "Elo (baseline)" }).getAttribute("href")).toBe(
      "/leaderboard?method=elo&include_confidence=true",
    );
    expect(screen.getByRole("link", { name: "Hide 95% CI" }).getAttribute("href")).toBe(
      "/leaderboard?method=bt",
    );
    expect(screen.getByRole("columnheader", { name: "95% CI" })).toBeDefined();
    expect(screen.getByText("Model A")).toBeDefined();
    expect(screen.getByText("1210.4")).toBeDefined();
    expect(screen.getByText(/1188\.9/)).toBeDefined();
  });

  it("shows the BT confidence toggle link when BT is selected", async () => {
    apiGetMock.mockResolvedValue({
      method: "bt",
      ci: false,
      bootstrap_rounds: null,
      models: [
        {
          model_id: "model-a",
          display_name: "Model A",
          rating: 1200,
          rating_lower: null,
          rating_upper: null,
          games_played: 5,
        },
      ],
    });

    const element = await LeaderboardPage({ searchParams: Promise.resolve({ method: "bt" }) });
    render(element);

    expect(apiGetMock).toHaveBeenCalledWith("/leaderboard?method=bt");
    expect(screen.getByText("95% CI")).toBeDefined();
    expect(screen.getByRole("link", { name: "Show 95% CI" }).getAttribute("href")).toBe(
      "/leaderboard?method=bt&include_confidence=true",
    );
    expect(screen.queryByRole("columnheader", { name: "95% CI" })).toBeNull();
  });

  it("requests Elo confidence intervals when enabled", async () => {
    apiGetMock.mockResolvedValue({
      method: "elo",
      ci: true,
      bootstrap_rounds: 200,
      models: [
        {
          model_id: "model-a",
          display_name: "Model A",
          rating: 1000,
          rating_lower: 980,
          rating_upper: 1020,
          games_played: 10,
        },
      ],
    });

    const element = await LeaderboardPage({
      searchParams: Promise.resolve({ method: "elo", include_confidence: "true" }),
    });
    render(element);

    expect(apiGetMock).toHaveBeenCalledWith("/leaderboard?method=elo&include_confidence=true");
    expect(screen.getByText("ELO")).toBeDefined();
    expect(screen.getByText(/200 bootstrap rounds/)).toBeDefined();
    expect(screen.getByRole("columnheader", { name: "95% CI" })).toBeDefined();
  });

  it("renders API error message when fetch fails", async () => {
    apiGetMock.mockRejectedValue(new Error("backend unavailable"));

    const element = await LeaderboardPage({ searchParams: Promise.resolve({ method: "bt" }) });
    render(element);

    expect(screen.getByText("backend unavailable")).toBeDefined();
    expect(screen.queryByText("No ratings yet")).toBeNull();
  });

  it("requests BT without confidence using correct endpoint without model_ratings", async () => {
    apiGetMock.mockResolvedValue({
      method: "bt",
      ci: false,
      bootstrap_rounds: null,
      models: [
        {
          model_id: "model-a",
          display_name: "Model A",
          rating: 1050,
          rating_lower: null,
          rating_upper: null,
          games_played: 8,
        },
      ],
    });

    const element = await LeaderboardPage({
      searchParams: Promise.resolve({ method: "bt" }),
    });
    render(element);

    expect(apiGetMock).toHaveBeenCalledWith("/leaderboard?method=bt");
    expect(apiGetMock).not.toHaveBeenCalledWith(
      expect.stringContaining("include_confidence"),
    );
    expect(screen.getByText("BT")).toBeDefined();
    expect(screen.getByText("Model A")).toBeDefined();
    expect(screen.queryByRole("columnheader", { name: "95% CI" })).toBeNull();
  });
});
