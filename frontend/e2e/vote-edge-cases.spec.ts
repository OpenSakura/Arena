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

function makeBattle(id: string): BattlePublic {
  return {
    id,
    task_id: `task-${id}`,
    source_text: "Vote edge-case source",
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

async function mockCompletedBattle(page: Page, battleId: string): Promise<void> {
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

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(makeBattle(battleId)),
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
        { event: "run.delta", data: { side: "A", text_delta: "Vote output A" } },
        { event: "run.delta", data: { side: "B", text_delta: "Vote output B" } },
        { event: "battle.completed", data: {} },
      ]),
    });
  });
}

test("submits tie votes with rubric tags and comment payload", async ({ page }) => {
  const votePayloads: unknown[] = [];

  await mockCompletedBattle(page, "battle-vote-tie");

  await page.route(/\/api\/v1\/battles\/[^/]+\/vote$/, async (route) => {
    votePayloads.push(route.request().postDataJSON());

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        vote_id: "vote-tie-1",
        battle_id: "battle-vote-tie",
        winner: "tie",
        reveal: {
          A: { model_id: "model-a", display_name: "Model A" },
          B: { model_id: "model-b", display_name: "Model B" },
        },
      }),
    });
  });

  await page.goto("/battle/new");

  await expect(page.getByText(/done/i)).toBeVisible({ timeout: 60_000 });

  await page.getByRole("button", { name: "Tie" }).click();
  await page.getByRole("button", { name: "accuracy" }).click();
  await page.getByRole("button", { name: "style" }).click();
  await page.getByLabel("Optional feedback").fill("Both outputs are strong in different dimensions.");

  await page.getByRole("button", { name: "Submit Vote" }).click();
  await expect(page.getByText("Model A", { exact: true }).first()).toBeVisible();

  expect(votePayloads).toHaveLength(1);
  const payload = votePayloads[0] as Record<string, unknown>;
  expect(payload).toMatchObject({
    winner: "tie",
    comment: "Both outputs are strong in different dimensions.",
    turnstile_token: null,
  });

  const rubric = payload.rubric as Record<string, unknown>;
  expect(rubric.tags).toEqual(expect.arrayContaining(["accuracy", "style"]));
});

test("shows conflict errors and allows retry with the same vote state", async ({ page }) => {
  const votePayloads: unknown[] = [];
  let submitCount = 0;

  await mockCompletedBattle(page, "battle-vote-conflict");

  await page.route(/\/api\/v1\/battles\/[^/]+\/vote$/, async (route) => {
    submitCount += 1;
    votePayloads.push(route.request().postDataJSON());

    if (submitCount === 1) {
      await route.fulfill({
        status: 409,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Vote already submitted for this battle" }),
      });
      return;
    }

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        vote_id: "vote-conflict-retry",
        battle_id: "battle-vote-conflict",
        winner: "A",
        reveal: {
          A: { model_id: "model-a", display_name: "Model A" },
          B: { model_id: "model-b", display_name: "Model B" },
        },
      }),
    });
  });

  await page.goto("/battle/new");

  await expect(page.getByText(/done/i)).toBeVisible({ timeout: 60_000 });

  await page.getByRole("button", { name: /Model A is better/i }).click();
  await page.getByRole("button", { name: "fluency" }).click();
  await page.getByLabel("Optional feedback").fill("Retrying after conflict");

  const submitVote = page.getByRole("button", { name: "Submit Vote" });
  await submitVote.click();

  await expect(page.getByText(/Vote already submitted for this battle/)).toBeVisible();
  await expect(page.getByText("Reveal")).toHaveCount(0);
  await expect(submitVote).toBeEnabled();

  await submitVote.click();

  await expect(page.getByText("Model A", { exact: true }).first()).toBeVisible();
  await expect(page.getByText(/Vote already submitted for this battle/)).toHaveCount(0);

  expect(votePayloads).toHaveLength(2);
  const firstPayload = votePayloads[0] as Record<string, unknown>;
  const secondPayload = votePayloads[1] as Record<string, unknown>;

  expect(firstPayload).toMatchObject({
    winner: "A",
    comment: "Retrying after conflict",
    turnstile_token: null,
  });
  expect(secondPayload).toMatchObject({
    winner: "A",
    comment: "Retrying after conflict",
    turnstile_token: null,
  });

  const firstRubric = firstPayload.rubric as Record<string, unknown>;
  const secondRubric = secondPayload.rubric as Record<string, unknown>;
  expect(firstRubric.tags).toEqual(expect.arrayContaining(["fluency"]));
  expect(secondRubric.tags).toEqual(expect.arrayContaining(["fluency"]));
});

test("submits only once when users double-click submit under latency", async ({ page }) => {
  const votePayloads: unknown[] = [];
  let voteCallCount = 0;

  await mockCompletedBattle(page, "battle-vote-idempotent");

  await page.route(/\/api\/v1\/battles\/[^/]+\/vote$/, async (route) => {
    voteCallCount += 1;
    votePayloads.push(route.request().postDataJSON());

    await new Promise<void>((resolve) => {
      setTimeout(resolve, 250);
    });

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        vote_id: "vote-idempotent-1",
        battle_id: "battle-vote-idempotent",
        winner: "A",
        reveal: {
          A: { model_id: "model-a", display_name: "Model A" },
          B: { model_id: "model-b", display_name: "Model B" },
        },
      }),
    });
  });

  await page.goto("/battle/new");

  await expect(page.getByText(/done/i)).toBeVisible({ timeout: 60_000 });

  await page.getByRole("button", { name: /Model A is better/i }).click();
  const submitVote = page.getByRole("button", { name: "Submit Vote" });
  await expect(submitVote).toBeEnabled();

  await submitVote.dblclick();

  await expect(page.getByText("Model A", { exact: true }).first()).toBeVisible();

  expect(voteCallCount).toBe(1);
  expect(votePayloads).toHaveLength(1);
  expect(votePayloads[0]).toMatchObject({
    winner: "A",
    turnstile_token: null,
  });
});
