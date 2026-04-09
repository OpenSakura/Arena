import { expect, test, type Page } from "@playwright/test";

type Side = "A" | "B";

type BattlePublic = {
  id: string;
  task_id: string;
  source_text: string;
  source_lang: string;
  target_lang: string;
  mode: string;
  status: string;
  run_a: {
    id: string;
    side: Side;
    output_text: string | null;
    stats: Record<string, unknown> | null;
    error_text: string | null;
  } | null;
  run_b: {
    id: string;
    side: Side;
    output_text: string | null;
    stats: Record<string, unknown> | null;
    error_text: string | null;
  } | null;
};

function makeBattle(id: string, sourceText: string): BattlePublic {
  return {
    id,
    task_id: `task-${id}`,
    source_text: sourceText,
    source_lang: "ja",
    target_lang: "zh",
    mode: "jp2zh_ab",
    status: "pending",
    run_a: {
      id: `${id}-run-a`,
      side: "A",
      output_text: null,
      stats: null,
      error_text: null,
    },
    run_b: {
      id: `${id}-run-b`,
      side: "B",
      output_text: null,
      stats: null,
      error_text: null,
    },
  };
}

function sseBody(events: Array<{ event: string; data: unknown }>): string {
  return events
    .map(({ event, data }) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)
    .join("");
}

async function mockPublicConfig(page: Page): Promise<void> {
  await page.route("**/api/v1/public-config", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ anon_vote_turnstile_required: false }),
    });
  });
}

test("surfaces battle.error details and can recover via restart", async ({ page }) => {
  let createCount = 0;

  await mockPublicConfig(page);

  await page.route("**/api/v1/battles", async (route) => {
    if (route.request().method() !== "POST") {
      await route.abort();
      return;
    }

    createCount += 1;
    const battle =
      createCount === 1
        ? makeBattle("battle-error", "Error-path JP source")
        : makeBattle("battle-recovered", "Recovered JP source");

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(battle),
    });
  });

  await page.route(/\/api\/v1\/battles\/[^/]+\/stream$/, async (route) => {
    const match = /\/battles\/([^/]+)\/stream$/.exec(route.request().url());
    const battleId = match?.[1] ?? "";

    const stream =
      battleId === "battle-error"
        ? sseBody([
          { event: "run.delta", data: { side: "A", text_delta: "Partial A" } },
          { event: "run.delta", data: { side: "B", text_delta: "Partial B" } },
          { event: "battle.error", data: { detail: "Model gateway timeout" } },
        ])
        : sseBody([
          { event: "run.delta", data: { side: "A", text_delta: "Recovered A" } },
          { event: "run.delta", data: { side: "B", text_delta: "Recovered B" } },
          { event: "battle.completed", data: {} },
        ]);

    await route.fulfill({
      status: 200,
      headers: {
        "content-type": "text/event-stream; charset=utf-8",
        "cache-control": "no-cache",
      },
      body: stream,
    });
  });

  await page.goto("/battle/new");

  await expect(page.getByText(/^error$/i)).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText("Partial A")).toBeVisible();
  await expect(page.getByText("Partial B")).toBeVisible();
  await expect(page.getByText("Battle error: Model gateway timeout")).toBeVisible();

  await page.getByRole("button", { name: /Model A is better/i }).click();
  await expect(page.getByRole("button", { name: "Submit Vote" })).toBeDisabled();

  await page.getByRole("button", { name: "Start another battle" }).click();

  await expect(page).toHaveURL(/\/battle\/new\?r=/);
  await expect(page.getByText(/^done$/i)).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText("Recovered JP source")).toBeVisible();
  await expect(page.getByText("Recovered A")).toBeVisible();
  await expect(page.getByText("Recovered B")).toBeVisible();

  // Restarting should clear stale error/winner state from the previous attempt.
  await expect(page.getByText("Battle error: Model gateway timeout")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Submit Vote" })).toBeDisabled();

  expect(createCount).toBe(2);
});

test("allows voting when stream terminal state is battle.failed", async ({ page }) => {
  const votePayloads: unknown[] = [];

  await mockPublicConfig(page);

  await page.route("**/api/v1/battles", async (route) => {
    if (route.request().method() !== "POST") {
      await route.abort();
      return;
    }

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(makeBattle("battle-failed", "Failure-path JP source")),
    });
  });

  await page.route(/\/api\/v1\/battles\/[^/]+\/stream$/, async (route) => {
    await route.fulfill({
      status: 200,
      headers: {
        "content-type": "text/event-stream; charset=utf-8",
        "cache-control": "no-cache",
      },
      body: sseBody([
        { event: "run.delta", data: { side: "A", text_delta: "A failed completion" } },
        { event: "run.delta", data: { side: "B", text_delta: "B failed completion" } },
        { event: "battle.failed", data: {} },
      ]),
    });
  });

  await page.route(/\/api\/v1\/battles\/[^/]+\/vote$/, async (route) => {
    votePayloads.push(route.request().postDataJSON());

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        vote_id: "vote-battle-failed",
        battle_id: "battle-failed",
        winner: "B",
        reveal: {
          A: { model_id: "model-a", display_name: "Model A" },
          B: { model_id: "model-b", display_name: "Model B" },
        },
      }),
    });
  });

  await page.goto("/battle/new");

  await expect(page.getByText(/^failed$/i)).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText("A failed completion")).toBeVisible();
  await expect(page.getByText("B failed completion")).toBeVisible();

  await page.getByLabel("Optional feedback").fill("B handled the partial output better.");
  await page.getByRole("button", { name: /Model B is better/i }).click();

  const submitVote = page.getByRole("button", { name: "Submit Vote" });
  await expect(submitVote).toBeEnabled();
  await submitVote.click();

  await expect(page.getByText("Model A", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("Model A", { exact: true })).toHaveCount(2);
  await expect(page.getByText("Model B", { exact: true })).toHaveCount(2);

  expect(votePayloads).toHaveLength(1);
  const payload = votePayloads[0] as Record<string, unknown>;
  expect(payload).toMatchObject({
    winner: "B",
    comment: "B handled the partial output better.",
    turnstile_token: null,
  });
});

test("recovers when initial battle bootstrap fails and retry starts cleanly", async ({ page }) => {
  let createCount = 0;

  await mockPublicConfig(page);

  await page.route("**/api/v1/battles", async (route) => {
    if (route.request().method() !== "POST") {
      await route.abort();
      return;
    }

    createCount += 1;
    if (createCount === 1) {
      await route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ detail: "No candidate model pair available" }),
      });
      return;
    }

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(makeBattle("battle-bootstrap-retry", "Retry-path JP source")),
    });
  });

  await page.route(/\/api\/v1\/battles\/[^/]+\/stream$/, async (route) => {
    await route.fulfill({
      status: 200,
      headers: {
        "content-type": "text/event-stream; charset=utf-8",
        "cache-control": "no-cache",
      },
      body: sseBody([
        { event: "run.delta", data: { side: "A", text_delta: "Recovered stream A" } },
        { event: "run.delta", data: { side: "B", text_delta: "Recovered stream B" } },
        { event: "battle.completed", data: {} },
      ]),
    });
  });

  await page.goto("/battle/new");

  await expect(page.getByText(/^error$/i)).toBeVisible({ timeout: 60_000 });
  await expect(
    page.getByText(/POST \/battles failed: 500 - No candidate model pair available/),
  ).toBeVisible();

  await page.getByRole("button", { name: "Start another battle" }).click();

  await expect(page).toHaveURL(/\/battle\/new\?r=/);
  await expect(page.getByText(/^done$/i)).toBeVisible({
    timeout: 60_000,
  });
  await expect(page.getByText("Retry-path JP source")).toBeVisible();
  await expect(page.getByText("Recovered stream A")).toBeVisible();
  await expect(page.getByText("Recovered stream B")).toBeVisible();
  await expect(
    page.getByText(/POST \/battles failed: 500 - No candidate model pair available/),
  ).toHaveCount(0);

  expect(createCount).toBe(2);
});

test("marks status as error on run.error and keeps voting disabled", async ({ page }) => {
  await mockPublicConfig(page);

  await page.route("**/api/v1/battles", async (route) => {
    if (route.request().method() !== "POST") {
      await route.abort();
      return;
    }

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(makeBattle("battle-run-error", "Run-error JP source")),
    });
  });

  await page.route(/\/api\/v1\/battles\/[^/]+\/stream$/, async (route) => {
    await route.fulfill({
      status: 200,
      headers: {
        "content-type": "text/event-stream; charset=utf-8",
        "cache-control": "no-cache",
      },
      body: sseBody([
        { event: "run.delta", data: { side: "A", text_delta: "Partial output A" } },
        { event: "run.delta", data: { side: "B", text_delta: "Partial output B" } },
        { event: "run.error", data: { side: "A", error: "Gateway timeout" } },
      ]),
    });
  });

  await page.goto("/battle/new");

  await expect(page.getByText(/^error$/i)).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText("Partial output A")).toBeVisible();
  await expect(page.getByText("Partial output B")).toBeVisible();

  await page.getByRole("button", { name: /Model A is better/i }).click();
  await expect(page.getByRole("button", { name: "Submit Vote" })).toBeDisabled();
});

test("surfaces stream transport failures and keeps voting disabled", async ({ page }) => {
  await mockPublicConfig(page);

  await page.route("**/api/v1/battles", async (route) => {
    if (route.request().method() !== "POST") {
      await route.abort();
      return;
    }

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(makeBattle("battle-stream-disconnect", "Disconnect JP source")),
    });
  });

  await page.route(/\/api\/v1\/battles\/[^/]+\/stream$/, async (route) => {
    await route.abort("failed");
  });

  await page.goto("/battle/new");

  await expect(page.getByText(/^error$/i)).toBeVisible({
    timeout: 60_000,
  });
  await expect(page.getByText(/SSE failed|Failed to fetch|Battle stream failed/i)).toBeVisible();

  await page.getByRole("button", { name: /Model B is better/i }).click();
  await expect(page.getByRole("button", { name: "Submit Vote" })).toBeDisabled();
});
