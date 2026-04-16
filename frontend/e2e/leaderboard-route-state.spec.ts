import { expect, test } from "@playwright/test";

import { mockSpaPublicConfig } from "./spa-auth";

test("keeps method and confidence toggles in URL across navigation and reload", async ({ page }) => {
  await mockSpaPublicConfig(page);

  await page.route(/\/api\/v1\/leaderboard(?:\?.*)?$/, async (route) => {
    const url = new URL(route.request().url());
    const method = url.searchParams.get("method") === "bt" ? "bt" : "elo";
    const includeConfidence = url.searchParams.get("include_confidence") === "true";

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        method,
        ci: includeConfidence,
        bootstrap_rounds: includeConfidence ? 2000 : null,
        models: [
          {
            model_id: "model-a",
            display_name: "Playwright Model A",
            rating: method === "bt" ? 1512.3 : 1501.1,
            rating_lower: includeConfidence ? 1490.1 : null,
            rating_upper: includeConfidence ? 1534.5 : null,
            games_played: 7,
          },
        ],
      }),
    });
  });

  await page.goto("/leaderboard?method=bt");

  await expect(page).toHaveURL(/\/leaderboard\?method=bt$/);
  await expect(page.getByText("Method: BT")).toBeVisible();
  await expect(page.getByRole("link", { name: "Show 95% CI" })).toBeVisible();

  await page.getByRole("link", { name: "Show 95% CI" }).click();

  await expect(page).toHaveURL(/\/leaderboard\?method=bt&include_confidence=true$/);
  await expect(page.getByText("Method: BT")).toBeVisible();
  await expect(page.getByRole("link", { name: "Hide 95% CI" })).toBeVisible();

  await page.reload();

  await expect(page).toHaveURL(/\/leaderboard\?method=bt&include_confidence=true$/);
  await expect(page.getByText("Method: BT")).toBeVisible();
  await expect(page.getByRole("link", { name: "Hide 95% CI" })).toBeVisible();

  await page.getByRole("link", { name: "Elo (baseline)" }).click();

  await expect(page).toHaveURL(/\/leaderboard\?method=elo&include_confidence=true$/);
  await expect(page.getByText("Method: ELO")).toBeVisible();
  await expect(page.getByRole("link", { name: "Hide 95% CI" })).toBeVisible();
});
