import { expect, test } from "@playwright/test";

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

test("streams outputs, reveals models after vote, and restarts cleanly", async ({ page }) => {
  let createCount = 0;
  const votePayloads: unknown[] = [];

  await page.route("**/api/v1/public-config", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ anon_vote_turnstile_required: false }),
    });
  });

  await page.route("**/api/v1/battles", async (route) => {
    if (route.request().method() !== "POST") {
      await route.abort();
      return;
    }

    createCount += 1;
    const battle =
      createCount === 1
        ? makeBattle("battle-1", "First JP source")
        : makeBattle("battle-2", "Second JP source");

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
      battleId === "battle-1"
        ? sseBody([
            { event: "run.delta", data: { side: "A", text_delta: "Alpha output (1)" } },
            { event: "run.delta", data: { side: "B", text_delta: "Beta output (1)" } },
            { event: "battle.completed", data: {} },
          ])
        : sseBody([
            { event: "run.delta", data: { side: "A", text_delta: "Alpha output (2)" } },
            { event: "run.delta", data: { side: "B", text_delta: "Beta output (2)" } },
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

  await page.route(/\/api\/v1\/battles\/[^/]+\/vote$/, async (route) => {
    votePayloads.push(route.request().postDataJSON());
    const match = /\/battles\/([^/]+)\/vote$/.exec(route.request().url());
    const battleId = match?.[1] ?? "battle-unknown";

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        vote_id: `vote-${battleId}`,
        battle_id: battleId,
        winner: "A",
        reveal: {
          A: { model_id: "model-a", display_name: "Model A" },
          B: { model_id: "model-b", display_name: "Model B" },
        },
      }),
    });
  });

  await page.goto("/battle/new");

  await expect(page.getByText(/^done$/i)).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText("First JP source")).toBeVisible();
  await expect(page.getByText("Alpha output (1)")).toBeVisible();
  await expect(page.getByText("Beta output (1)")).toBeVisible();

  await page.getByRole("button", { name: /Model A is better/i }).click();
  await expect(page.getByRole("button", { name: "Submit Vote" })).toBeEnabled();
  await page.getByRole("button", { name: "Submit Vote" }).click();

  await expect(page.getByText("Model A", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("Model A", { exact: true })).toHaveCount(2);
  await expect(page.getByText("Model B", { exact: true })).toHaveCount(2);

  expect(votePayloads).toHaveLength(1);
  const firstVote = votePayloads[0] as Record<string, unknown>;
  expect(firstVote).toMatchObject({
    winner: "A",
    comment: null,
    turnstile_token: null,
  });

  await page.getByRole("button", { name: "Start another battle" }).click();

  await expect(page).toHaveURL(/\/battle\/new\?r=/);
  await expect(page.getByText(/^done$/i)).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText("Second JP source")).toBeVisible();
  await expect(page.getByText("Alpha output (2)")).toBeVisible();
  await expect(page.getByText("Beta output (2)")).toBeVisible();

  // A restart should clear winner/reveal state from the previous battle.
  await expect(page.getByRole("button", { name: "Submit Vote" })).toBeDisabled();
  await expect(page.getByText("Model A", { exact: true })).toHaveCount(1);

  expect(createCount).toBe(2);
});

test("loads an existing completed battle id without creating a new one", async ({ page }) => {
  let createCount = 0;
  const votePayloads: unknown[] = [];

  await page.route("**/api/v1/public-config", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ anon_vote_turnstile_required: false }),
    });
  });

  await page.route("**/api/v1/battles", async (route) => {
    if (route.request().method() === "POST") {
      createCount += 1;
      await route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Unexpected create call" }),
      });
      return;
    }

    await route.abort();
  });

  await page.route(/\/api\/v1\/battles\/battle-existing$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: "battle-existing",
        task_id: "task-existing",
        source_text: "Existing battle source",
        source_lang: "ja",
        target_lang: "zh",
        mode: "jp2zh_ab",
        status: "completed",
        run_a: {
          id: "battle-existing-run-a",
          side: "A",
          output_text: "Persisted output A",
          stats: null,
          error_text: null,
        },
        run_b: {
          id: "battle-existing-run-b",
          side: "B",
          output_text: "Persisted output B",
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
      body: sseBody([
        {
          event: "run.delta",
          data: {
            side: "A",
            text_delta: "REPLAY_SHOULD_NOT_APPEAR",
            replay: true,
            chunk_index: 0,
          },
        },
        {
          event: "run.delta",
          data: {
            side: "B",
            text_delta: "REPLAY_SHOULD_NOT_APPEAR",
            replay: true,
            chunk_index: 0,
          },
        },
        { event: "battle.completed", data: { replay: true } },
      ]),
    });
  });

  await page.route(/\/api\/v1\/battles\/[^/]+\/vote$/, async (route) => {
    votePayloads.push(route.request().postDataJSON());

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        vote_id: "vote-existing",
        battle_id: "battle-existing",
        winner: "B",
        reveal: {
          A: { model_id: "model-a", display_name: "Existing Model A" },
          B: { model_id: "model-b", display_name: "Existing Model B" },
        },
      }),
    });
  });

  await page.goto("/battle/battle-existing");

  await expect(page.getByText(/^done$/i)).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText("Existing battle source")).toBeVisible();
  await expect(page.getByText("Persisted output A")).toBeVisible();
  await expect(page.getByText("Persisted output B")).toBeVisible();
  await expect(page.getByText("REPLAY_SHOULD_NOT_APPEAR")).toHaveCount(0);

  await page.getByRole("button", { name: /Model B is better/i }).click();
  await page.getByRole("button", { name: "Submit Vote" }).click();

  await expect(page.getByText("Model A", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("Existing Model A", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("Existing Model B", { exact: true }).first()).toBeVisible();

  expect(createCount).toBe(0);
  expect(votePayloads).toHaveLength(1);
  const payload = votePayloads[0] as Record<string, unknown>;
  expect(payload).toMatchObject({ winner: "B" });
});
