import { expect, test } from "@playwright/test";

test("blocks anonymous battle creation when backend requires Turnstile but site key is missing", async ({
  page,
}) => {
  test.skip(
    process.env.NEXT_PUBLIC_TURNSTILE_SITE_KEY !== "",
    "Run with NEXT_PUBLIC_TURNSTILE_SITE_KEY='' to validate the missing-key branch.",
  );

  await page.route("**/api/auth/session*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: "null",
    });
  });

  await page.route("**/api/v1/public-config", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ anon_battle_turnstile_required: true }),
    });
  });

  await page.route("**/api/v1/battles", async (route) => {
    await route.abort();
  });

  await page.goto("/battle/new");

  await expect(page.getByText("Verification Required")).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText(/Backend requires Turnstile for anonymous battles/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Solve Turnstile" })).toHaveCount(0);
});
