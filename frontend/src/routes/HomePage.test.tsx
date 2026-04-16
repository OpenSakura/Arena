// @vitest-environment jsdom

import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import HomePage from "./HomePage";

describe("HomePage", () => {
  it("renders the main heading", () => {
    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>
    );

    expect(screen.getByRole("heading", { name: /Open\s*Sakura\s*Arena/i })).toBeDefined();
  });

  it("contains CTA links to battle and leaderboard", () => {
    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>
    );

    const startBattleLink = screen.getByRole("link", { name: /Start a Battle/i });
    expect(startBattleLink.getAttribute("href")).toBe("/battle/new");

    const viewLeaderboardLink = screen.getByRole("link", { name: /View Leaderboard/i });
    expect(viewLeaderboardLink.getAttribute("href")).toBe("/leaderboard");
  });
});
