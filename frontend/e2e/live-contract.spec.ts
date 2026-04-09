import { expect, test } from "@playwright/test";

test("completes a live battle, submits vote, and updates leaderboard", async ({ page }) => {
  test.skip(
    process.env.PW_ENABLE_LIVE_STACK !== "1",
    "Set PW_ENABLE_LIVE_STACK=1 to run live backend contract smoke.",
  );

  await page.goto("/battle/new");

  await expect(page.getByText(/done/i)).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText("E2E live contract source text.")).toBeVisible();
  await expect(page.getByText("E2E Alpha translation from mock gateway.")).toBeVisible();
  await expect(page.getByText("E2E Beta translation from mock gateway.")).toBeVisible();

  await page.getByRole("button", { name: "Tie" }).click();
  const submitVote = page.getByRole("button", { name: "Submit Vote" });
  await expect(submitVote).toBeEnabled();
  await submitVote.click();

  await expect(page.getByText("Model A", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("Playwright Live Model A")).toBeVisible();
  await expect(page.getByText("Playwright Live Model B")).toBeVisible();

  await page.goto("/leaderboard");

  await expect(page.getByText("Playwright Live Model A")).toBeVisible();
  await expect(page.getByText("Playwright Live Model B")).toBeVisible();
  await expect(page.getByText(/Method: ELO/i)).toBeVisible();

  const modelARow = page.locator("tr").filter({ hasText: "Playwright Live Model A" });
  const modelBRow = page.locator("tr").filter({ hasText: "Playwright Live Model B" });

  await expect(modelARow).toContainText("1");
  await expect(modelBRow).toContainText("1");
});
