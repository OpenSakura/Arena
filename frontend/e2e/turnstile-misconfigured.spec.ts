import { expect, test } from "@playwright/test";

test("blocks anonymous submit when backend requires Turnstile but site key is missing", async ({
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
      body: JSON.stringify({ anon_vote_turnstile_required: true }),
    });
  });

  await page.route("**/api/v1/battles", async (route) => {
    if (route.request().method() !== "POST") {
      await route.abort();
      return;
    }

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: "battle-turnstile-missing-key",
        task_id: "task-battle-turnstile-missing-key",
        source_text: "Turnstile missing-key source",
        source_lang: "ja",
        target_lang: "zh",
        mode: "jp2zh_ab",
        status: "completed",
        run_a: {
          id: "run-a",
          side: "A",
          output_text: "Output A",
          stats: null,
          error_text: null,
        },
        run_b: {
          id: "run-b",
          side: "B",
          output_text: "Output B",
          stats: null,
          error_text: null,
        },
      }),
    });
  });

  await page.route(/\/api\/v1\/battles\/[^/]+\/stream$/, async (route) => {
    await route.fulfill({
      status: 200,
      headers: {
        "content-type": "text/event-stream; charset=utf-8",
        "cache-control": "no-cache",
      },
      body: "",
    });
  });

  await page.goto("/battle/new");

  await expect(page.getByText(/done/i)).toBeVisible({
    timeout: 60_000,
  });
  await expect(page.getByText(/Backend requires Turnstile for anonymous voting/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Solve Turnstile" })).toHaveCount(0);

  await page.getByRole("button", { name: /Model A is better/i }).click();
  await expect(page.getByRole("button", { name: "Submit Vote" })).toBeDisabled();
});
