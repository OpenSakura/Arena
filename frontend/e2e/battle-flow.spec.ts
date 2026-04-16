import { expect, test, type Route } from "@playwright/test";

import { mockSpaAuthenticatedSession } from "./spa-auth";

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

function makeBattle(id: string, sourceText: string): BattlePublic {
  return {
    id,
    task_id: `task-${id}`,
    source_text: sourceText,
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

async function mockAuthenticatedSession(
  page: import("@playwright/test").Page,
  accessToken: string,
): Promise<void> {
  await mockSpaAuthenticatedSession(page, {
    accessToken,
    profile: {
      sub: "battle-e2e-user",
      name: "Battle E2E",
      email: "battle-e2e@example.com",
    },
  });
}

async function mockBattleDetails(
  page: import("@playwright/test").Page,
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

test("streams outputs, reveals models after vote, and restarts cleanly", async ({ page }) => {
  let createCount = 0;
  const votePayloads: Array<{ authHeader: string | undefined; payload: unknown }> = [];
  const createAuthHeaders: Array<string | undefined> = [];
  const battlesById = new Map<string, BattlePublic>();

  await mockAuthenticatedSession(page, "battle-flow-access-token");

  await page.route("**/api/v1/battles", async (route) => {
    if (await handleCorsIfPreflight(route)) return;
    if (route.request().method() !== "POST") {
      await route.abort();
      return;
    }

    createAuthHeaders.push(route.request().headers()["authorization"]);
    createCount += 1;
    const battle =
      createCount === 1
        ? makeBattle("battle-1", "First JP source")
        : makeBattle("battle-2", "Second JP source");
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
        ...CORS_HEADERS,
      },
      body: stream,
    });
  });

  await page.route(/\/api\/v1\/battles\/[^/]+\/vote$/, async (route) => {
    if (await handleCorsIfPreflight(route)) return;
    votePayloads.push({
      authHeader: route.request().headers()["authorization"],
      payload: route.request().postDataJSON(),
    });
    const match = /\/battles\/([^/]+)\/vote$/.exec(route.request().url());
    const battleId = match?.[1] ?? "battle-unknown";

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      headers: CORS_HEADERS,
      body: JSON.stringify({
        vote_id: `vote-${battleId}`,
        battle_id: battleId,
        winner: "A",
        reveal: {
          A: { model_id: "model-a", display_name: "Revealed Model A" },
          B: { model_id: "model-b", display_name: "Revealed Model B" },
        },
      }),
    });
  });

  await page.goto("/battle/new");

  await expect(page.getByText(/^complete$/i)).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText("First JP source")).toBeVisible();
  await expect(page.getByText("Alpha output (1)")).toBeVisible();
  await expect(page.getByText("Beta output (1)")).toBeVisible();

  await page.getByRole("button", { name: /Model A is better/i }).click();
  await expect(page.getByRole("button", { name: "Submit Vote" })).toBeEnabled();
  await page.getByRole("button", { name: "Submit Vote" }).click();

  await expect(page.getByText("Revealed Model A", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("Revealed Model B", { exact: true }).first()).toBeVisible();

  expect(votePayloads).toHaveLength(1);
  const firstVote = votePayloads[0]?.payload as Record<string, unknown>;
  expect(votePayloads[0]?.authHeader).toBe("Bearer battle-flow-access-token");
  expect(firstVote).toMatchObject({
    winner: "A",
    comment: null,
  });

  await page.getByRole("button", { name: "Start another battle" }).click();

  await expect(page).toHaveURL(/\/battle\/battle-2$/);
  await expect(page.getByText(/^complete$/i)).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText("Second JP source")).toBeVisible();
  await expect(page.getByText("Alpha output (2)")).toBeVisible();
  await expect(page.getByText("Beta output (2)")).toBeVisible();

  // A restart should clear winner/reveal state from the previous battle.
  await expect(page.getByRole("button", { name: "Submit Vote" })).toBeDisabled();
  await expect(page.getByText("Revealed Model A", { exact: true })).toHaveCount(0);

  expect(createCount).toBe(2);
  expect(createAuthHeaders).toEqual([
    "Bearer battle-flow-access-token",
    "Bearer battle-flow-access-token",
  ]);
});

test("loads an existing completed battle id without creating a new one", async ({ page }) => {
  let createCount = 0;
  const votePayloads: Array<{ authHeader: string | undefined; payload: unknown }> = [];

  await mockAuthenticatedSession(page, "battle-existing-access-token");

  await page.route("**/api/v1/battles", async (route) => {
    if (await handleCorsIfPreflight(route)) return;
    if (route.request().method() === "POST") {
      createCount += 1;
      await route.fulfill({
        status: 500,
        contentType: "application/json",
        headers: CORS_HEADERS,
        body: JSON.stringify({ detail: "Unexpected create call" }),
      });
      return;
    }

    await route.abort();
  });

  await page.route(/\/api\/v1\/battles\/battle-existing$/, async (route) => {
    if (await handleCorsIfPreflight(route)) return;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      headers: CORS_HEADERS,
      body: JSON.stringify({
        id: "battle-existing",
        task_id: "task-existing",
        source_text: "Existing battle source",
        source_lang: "ja",
        target_lang: "zh",
        mode: "jp2zh_ab",
        status: "completed",
        retry_allowed: false,
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
    if (await handleCorsIfPreflight(route)) return;
    await route.fulfill({
      status: 200,
      headers: {
        "content-type": "text/event-stream; charset=utf-8",
        "cache-control": "no-cache",
        ...CORS_HEADERS,
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
    if (await handleCorsIfPreflight(route)) return;
    votePayloads.push({
      authHeader: route.request().headers()["authorization"],
      payload: route.request().postDataJSON(),
    });

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      headers: CORS_HEADERS,
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

  await expect(page.getByText(/^complete$/i)).toBeVisible({ timeout: 60_000 });
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
  expect(votePayloads[0]?.authHeader).toBe("Bearer battle-existing-access-token");
  const payload = votePayloads[0]?.payload as Record<string, unknown>;
  expect(payload).toMatchObject({ winner: "B" });
});
