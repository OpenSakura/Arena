import { expect, test } from "@playwright/test";
import fs from "fs/promises";
import path from "path";

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

test("judge_type filters update URL and request payload", async ({ page }) => {
  await mockSpaPublicConfig(page);
  const requests: { url: string; judge_type: string | null }[] = [];

  await page.route(/\/api\/v1\/leaderboard(?:\?.*)?$/, async (route) => {
    const url = new URL(route.request().url());
    const method = url.searchParams.get("method") === "bt" ? "bt" : "elo";
    const judgeType = url.searchParams.get("judge_type");
    
    requests.push({ url: url.toString(), judge_type: judgeType });

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        method,
        ci: false,
        bootstrap_rounds: null,
        vote_source_counts: {
          human: judgeType === "bot" ? 0 : 50,
          bot: judgeType === "human" ? 0 : 20,
          total: judgeType === "bot" ? 20 : (judgeType === "human" ? 50 : 70),
        },
        models: [
          {
            model_id: "model-a",
            display_name: "Playwright Model A",
            rating: 1500,
            rating_lower: null,
            rating_upper: null,
            games_played: 10,
          },
        ],
      }),
    });
  });

  await page.goto("/leaderboard");

  await expect(page).toHaveURL(/\/leaderboard$/);
  await expect(page.getByRole("link", { name: "All votes" })).toBeVisible();
  await expect(page.getByText("70 total")).toBeVisible();

  const evidenceDir = path.resolve(__dirname, "../../.omo/evidence");
  await fs.mkdir(evidenceDir, { recursive: true });
  await page.screenshot({ path: path.join(evidenceDir, "task-11-leaderboard-all.png") });

  await page.getByRole("link", { name: "Bot votes" }).click();
  await expect(page).toHaveURL(/\/leaderboard\?.*judge_type=bot/);
  await expect(page.getByText("20 total")).toBeVisible();

  await page.screenshot({ path: path.join(evidenceDir, "task-11-leaderboard-bot.png") });
  
  await fs.writeFile(
    path.join(evidenceDir, "task-11-leaderboard-requests.json"),
    JSON.stringify(requests, null, 2)
  );
  
  expect(requests[0].judge_type).toBe("all");
  expect(requests[requests.length - 1].judge_type).toBe("bot");
});
