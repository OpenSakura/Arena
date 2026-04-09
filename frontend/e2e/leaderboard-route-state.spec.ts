import { expect, test } from "@playwright/test";

test("keeps method and confidence toggles in URL across navigation and reload", async ({ page }) => {
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

  await expect(page).toHaveURL(/\/leaderboard\?method=elo$/);
  await expect(page.getByText("Method: ELO")).toBeVisible();
  await expect(page.getByRole("link", { name: "Show 95% CI" })).toBeVisible();
});
