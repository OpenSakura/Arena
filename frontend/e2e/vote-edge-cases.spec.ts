import { expect, test, type Page, type Route } from "@playwright/test";

import { mockSpaAuthenticatedSession } from "./spa-auth";

const CORS_HEADERS: Record<string, string> = {
  "access-control-allow-origin": "http://localhost:13000",
  "access-control-allow-credentials": "true",
  "access-control-allow-methods": "GET, POST, PUT, DELETE, OPTIONS",
  "access-control-allow-headers": "authorization, content-type, accept",
};

async function handleCorsIfPreflight(route: Route): Promise<boolean> {
  if (route.request().method() === "OPTIONS") {
    await route.fulfill({ status: 204, headers: CORS_HEADERS });
    return true;
  }
  return false;
}

async function mockAuthenticatedSession(page: Page): Promise<void> {
  await mockSpaAuthenticatedSession(page, {
    profile: {
      sub: "vote-e2e-user",
      name: "Vote E2E",
      email: "vote-e2e@example.com",
    },
  });
}

async function mockBattleDetails(
  page: Page,
  battlesById: Map<string, BattlePublic>,
): Promise<void> {
  await page.route(/\/api\/v1\/battles\/[^/]+$/, async (route) => {
    if (await handleCorsIfPreflight(route)) return;
    if (route.request().method() !== "GET") {
      await route.abort();
      return;
    }

    const match = /\/battles\/([^/?]+)$/.exec(route.request().url());
    const battleId = match?.[1] ?? "";
    const battle = battlesById.get(battleId);

    await route.fulfill({
      status: battle ? 200 : 404,
      contentType: "application/json",
      headers: CORS_HEADERS,
      body: JSON.stringify(
        battle ?? { detail: `Mock battle not found: ${battleId}` },
      ),
    });
  });
}

type Side = "A" | "B";

type BattlePublic = {
  id: string;
  task_id: string;
  source_text: string;
  source_lang: string;
  target_lang: string;
  mode: string;
  status: string;
  retry_allowed: boolean;
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
    retry_allowed: false,
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
  const battlesById = new Map<string, BattlePublic>();

  await page.route("**/api/v1/battles", async (route) => {
    if (await handleCorsIfPreflight(route)) return;
    if (route.request().method() !== "POST") {
      await route.abort();
      return;
    }

    const battle = makeBattle(battleId);
    battlesById.set(battle.id, battle);

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      headers: CORS_HEADERS,
      body: JSON.stringify(battle),
    });
  });

  await mockBattleDetails(page, battlesById);

  await page.route(/\/api\/v1\/battles\/[^/]+\/stream$/, async (route) => {
    if (await handleCorsIfPreflight(route)) return;
    await route.fulfill({
      status: 200,
      headers: {
        "content-type": "text/event-stream; charset=utf-8",
        "cache-control": "no-cache",
        ...CORS_HEADERS,
      },
      body: sseBody([
        { event: "run.delta", data: { side: "A", text_delta: "Vote output A" } },
        { event: "run.delta", data: { side: "B", text_delta: "Vote output B" } },
        { event: "battle.completed", data: {} },
      ]),
    });
  });
}

async function expectVoteControlsReady(page: Page): Promise<void> {
  await expect(page.getByRole("button", { name: "Submit Vote" })).toBeDisabled({ timeout: 60_000 });
  await expect(page.getByRole("button", { name: "Tie" })).toBeVisible();
}

test("submits tie votes with rubric tags and comment payload", async ({ page }) => {
  const votePayloads: Array<{ csrfHeader: string | undefined; payload: unknown }> = [];

  await mockAuthenticatedSession(page);
  await mockCompletedBattle(page, "battle-vote-tie");

  await page.route(/\/api\/v1\/battles\/[^/]+\/vote$/, async (route) => {
    if (await handleCorsIfPreflight(route)) return;
    votePayloads.push({
      csrfHeader: route.request().headers()["x-csrf-token"],
      payload: route.request().postDataJSON(),
    });

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      headers: CORS_HEADERS,
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

  await expectVoteControlsReady(page);

  await page.getByRole("button", { name: "Tie" }).click();
  await page.getByRole("button", { name: "Accuracy" }).click();
  await page.getByRole("button", { name: "Style" }).click();
  await page.getByRole("button", { name: "Knowledge" }).click();

  const terminologyButton = page.getByRole("button", { name: "Terminology" });
  await terminologyButton.hover();
  await expect(page.getByRole("tooltip", { name: /Accurate translation of proper nouns/ })).toBeVisible();
  await terminologyButton.click();

  const refusalButton = page.getByRole("button", { name: "Refusal" });
  await refusalButton.hover();
  await expect(page.getByRole("tooltip", { name: /Refused to translate or provide a response/ })).toBeVisible();
  await refusalButton.click();

  await page.getByLabel("Optional feedback").fill("Both outputs are strong in different dimensions.");

  await page.getByRole("button", { name: "Submit Vote" }).click();
  await expect(page.getByText("Model A", { exact: true }).first()).toBeVisible();

  expect(votePayloads).toHaveLength(1);
  expect(votePayloads[0]?.csrfHeader).toBe("playwright-csrf-token");
  const payload = votePayloads[0]?.payload as Record<string, unknown>;
  expect(payload).toMatchObject({
    winner: "tie",
    comment: "Both outputs are strong in different dimensions.",
  });

  const rubric = payload.rubric as Record<string, unknown>;
  expect(rubric.tags).toEqual(
    expect.arrayContaining(["accuracy", "style", "knowledge", "terminology", "refusal"]),
  );
});

test("shows conflict errors and allows retry with the same vote state", async ({ page }) => {
  const votePayloads: Array<{ csrfHeader: string | undefined; payload: unknown }> = [];
  let submitCount = 0;

  await mockAuthenticatedSession(page);
  await mockCompletedBattle(page, "battle-vote-conflict");

  await page.route(/\/api\/v1\/battles\/[^/]+\/vote$/, async (route) => {
    if (await handleCorsIfPreflight(route)) return;
    submitCount += 1;
    votePayloads.push({
      csrfHeader: route.request().headers()["x-csrf-token"],
      payload: route.request().postDataJSON(),
    });

    if (submitCount === 1) {
      await route.fulfill({
        status: 409,
        contentType: "application/json",
        headers: CORS_HEADERS,
        body: JSON.stringify({ detail: "Vote already submitted for this battle" }),
      });
      return;
    }

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      headers: CORS_HEADERS,
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

  await expectVoteControlsReady(page);

  await page.getByRole("button", { name: /Model A is better/i }).click();
  await page.getByRole("button", { name: "Fluency" }).click();
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
  expect(votePayloads[0]?.csrfHeader).toBe("playwright-csrf-token");
  expect(votePayloads[1]?.csrfHeader).toBe("playwright-csrf-token");
  const firstPayload = votePayloads[0]?.payload as Record<string, unknown>;
  const secondPayload = votePayloads[1]?.payload as Record<string, unknown>;

  expect(firstPayload).toMatchObject({
    winner: "A",
    comment: "Retrying after conflict",
  });
  expect(secondPayload).toMatchObject({
    winner: "A",
    comment: "Retrying after conflict",
  });

  const firstRubric = firstPayload.rubric as Record<string, unknown>;
  const secondRubric = secondPayload.rubric as Record<string, unknown>;
  expect(firstRubric.tags).toEqual(expect.arrayContaining(["fluency"]));
  expect(secondRubric.tags).toEqual(expect.arrayContaining(["fluency"]));
});

test("submits only once when users double-click submit under latency", async ({ page }) => {
  const votePayloads: Array<{ csrfHeader: string | undefined; payload: unknown }> = [];
  let voteCallCount = 0;

  await mockAuthenticatedSession(page);
  await mockCompletedBattle(page, "battle-vote-idempotent");

  await page.route(/\/api\/v1\/battles\/[^/]+\/vote$/, async (route) => {
    if (await handleCorsIfPreflight(route)) return;
    voteCallCount += 1;
    votePayloads.push({
      csrfHeader: route.request().headers()["x-csrf-token"],
      payload: route.request().postDataJSON(),
    });

    await new Promise<void>((resolve) => {
      setTimeout(resolve, 250);
    });

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      headers: CORS_HEADERS,
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

  await expectVoteControlsReady(page);

  await page.getByRole("button", { name: /Model A is better/i }).click();
  const submitVote = page.getByRole("button", { name: "Submit Vote" });
  await expect(submitVote).toBeEnabled();

  await submitVote.dblclick();

  await expect(page.getByText("Model A", { exact: true }).first()).toBeVisible();

  expect(voteCallCount).toBe(1);
  expect(votePayloads).toHaveLength(1);
  expect(votePayloads[0]?.csrfHeader).toBe("playwright-csrf-token");
  expect(votePayloads[0]?.payload).toMatchObject({
    winner: "A",
  });
});
