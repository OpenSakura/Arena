import { expect, test } from "@playwright/test";

test("completes a live battle, submits vote, and updates leaderboard", async ({ page }) => {
  const backendBaseUrl = `http://localhost:${process.env.PW_BACKEND_PORT ?? "28000"}/api/v1`;

  test.skip(
    process.env.PW_ENABLE_LIVE_STACK !== "1",
    "Set PW_ENABLE_LIVE_STACK=1 to run live backend contract smoke.",
  );

  await page.goto("/");
  await page.getByRole("button", { name: "Login" }).click();

  const authForm = page.getByRole("main", { name: /authentication form/i });

  const userInput = authForm.locator('input[name="uidField"]');
  await userInput.waitFor({ state: "visible", timeout: 60_000 });
  await userInput.fill("akadmin");
  await userInput.press("Enter");

  const identifyAction = authForm.getByRole("button", { name: /log in|continue/i });
  await identifyAction.waitFor({ state: "visible", timeout: 30_000 });
  await expect(identifyAction).toBeEnabled({ timeout: 30_000 });
  await identifyAction.click();

  const passwordInput = authForm.locator('input[name="password"]');
  await passwordInput.waitFor({ state: "visible", timeout: 60_000 });
  await passwordInput.fill("password1234");

  const passwordAction = authForm.getByRole("button", { name: /log in|continue/i });
  await passwordAction.waitFor({ state: "visible", timeout: 30_000 });
  await expect(passwordAction).toBeEnabled({ timeout: 30_000 });
  await passwordAction.click();

  await page.getByRole("button", { name: "Logout" }).waitFor({ state: "visible", timeout: 60_000 });

  await page.goto("/battle/new");

  await expect(page.getByText(/complete/i)).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText("E2E live contract source text.")).toBeVisible();
  await expect(page.getByText("E2E Alpha translation from mock gateway.")).toBeVisible();
  await expect(page.getByText("E2E Beta translation from mock gateway.")).toBeVisible();

  await page.getByRole("button", { name: "Tie" }).click();
  const submitVote = page.getByRole("button", { name: "Submit Vote" });
  await expect(submitVote).toBeEnabled();
  await submitVote.click();

  await expect(page.getByText("Model A", { exact: true }).first()).toBeVisible();
  await expect(page.locator(".badge-sakura").filter({ hasText: "Playwright Live Model A" })).toBeVisible();
  await expect(page.locator(".badge-sakura").filter({ hasText: "Playwright Live Model B" })).toBeVisible();

  await expect
    .poll(async () => {
      const response = await page.request.get(`${backendBaseUrl}/leaderboard?method=elo`);
      if (!response.ok()) {
        return "error";
      }

      const payload = (await response.json()) as {
        models?: Array<{ display_name?: string; games_played?: number }>;
      };
      const rows = payload.models ?? [];
      const rowA = rows.find((row) => row.display_name === "Playwright Live Model A");
      const rowB = rows.find((row) => row.display_name === "Playwright Live Model B");
      return `${rowA?.games_played ?? 0},${rowB?.games_played ?? 0}`;
    }, { timeout: 15_000 })
    .toBe("1,1");

  await page.goto("/leaderboard");

  const modelARow = page.locator("tr").filter({ hasText: "Playwright Live Model A" });
  const modelBRow = page.locator("tr").filter({ hasText: "Playwright Live Model B" });

  await expect(page.getByText(/Method: ELO/i)).toBeVisible();
  await expect(modelARow).toBeVisible();
  await expect(modelBRow).toBeVisible();
  await expect(modelARow).toContainText("1");
  await expect(modelBRow).toContainText("1");
});
