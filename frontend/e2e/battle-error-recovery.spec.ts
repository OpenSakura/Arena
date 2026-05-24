import { expect, test, type Page, type Route } from "@playwright/test";

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

async function mockAuthenticatedSession(page: Page): Promise<void> {
  await mockSpaAuthenticatedSession(page, {
    profile: {
      sub: "battle-e2e-user",
      name: "Battle E2E",
      email: "battle-e2e@example.com",
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

test("surfaces battle.error details and can recover via restart", async ({ page }) => {
  let createCount = 0;
  const createCsrfHeaders: Array<string | undefined> = [];
  const battlesById = new Map<string, BattlePublic>();

  await mockAuthenticatedSession(page);

  await mockBattleDetails(page, battlesById);

  await page.route("**/api/v1/battles", async (route) => {
    if (await handleCorsIfPreflight(route)) return;
    if (route.request().method() !== "POST") {
      await route.abort();
      return;
    }

    createCsrfHeaders.push(route.request().headers()["x-csrf-token"]);
    createCount += 1;
    const battle =
      createCount === 1
        ? makeBattle("battle-error", "Error-path JP source")
        : makeBattle("battle-recovered", "Recovered JP source");
    battlesById.set(battle.id, battle);

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      headers: CORS_HEADERS,
      body: JSON.stringify(battle),
    });
  });

  await page.route(/\/api\/v1\/battles\/[^/]+\/stream$/, async (route) => {
    if (await handleCorsIfPreflight(route)) return;
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
        ...CORS_HEADERS,
      },
      body: stream,
    });
  });

  await page.goto("/battle/new");

  await expect(page.getByText(/^error$/i)).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText("Partial A")).toBeVisible({ timeout: 5000 });
  await expect(page.getByText("Partial B")).toBeVisible();
  await expect(page.getByText("Battle error: Model gateway timeout")).toBeVisible();
  await expect(page.getByRole("button", { name: /Model A is better/i })).toHaveCount(0);
  await expect(page.getByLabel("Optional feedback")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Submit Vote" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Retry Battle" })).toHaveCount(0);

  await expect(page.getByRole("button", { name: "Start another battle" })).toBeVisible();
  await page.goto("/battle/new?r=recovered");

  await expect(page).toHaveURL(/\/battle\/battle-recovered$/);
  await expect(page.getByText(/^complete$/i)).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText("Recovered JP source")).toBeVisible();
  await expect(page.getByText("Recovered A")).toBeVisible();
  await expect(page.getByText("Recovered B")).toBeVisible();

  // Restarting should clear stale error/winner state from the previous attempt.
  await expect(page.getByText("Battle error: Model gateway timeout")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Submit Vote" })).toBeDisabled();

  expect(createCount).toBe(2);
  expect(createCsrfHeaders).toEqual(["playwright-csrf-token", "playwright-csrf-token"]);
});

test("disables voting when stream terminal state is battle.failed", async ({ page }) => {
  const battlesById = new Map<string, BattlePublic>();

  await mockAuthenticatedSession(page);

  await mockBattleDetails(page, battlesById);

  await page.route("**/api/v1/battles", async (route) => {
    if (await handleCorsIfPreflight(route)) return;
    if (route.request().method() !== "POST") {
      await route.abort();
      return;
    }

    const battle = makeBattle("battle-failed", "Failure-path JP source");
    battlesById.set(battle.id, battle);

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      headers: CORS_HEADERS,
      body: JSON.stringify(battle),
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
        { event: "run.delta", data: { side: "A", text_delta: "A failed completion" } },
        { event: "run.delta", data: { side: "B", text_delta: "B failed completion" } },
        { event: "battle.failed", data: {} },
      ]),
    });
  });

  await page.route(/\/api\/v1\/battles\/[^/]+\/vote$/, async (route) => {
    await route.abort();
  });

  await page.goto("/battle/new");

  await expect(page.getByText(/^failed$/i)).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText("A failed completion")).toBeVisible();
  await expect(page.getByText("B failed completion")).toBeVisible();
  await expect(page.getByRole("button", { name: /Model B is better/i })).toHaveCount(0);
  await expect(page.getByLabel("Optional feedback")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Submit Vote" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Retry Battle" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Start another battle" })).toBeVisible();
});

test("recovers when initial battle bootstrap fails and retry starts cleanly", async ({ page }) => {
  let createCount = 0;
  const createCsrfHeaders: Array<string | undefined> = [];
  const battlesById = new Map<string, BattlePublic>();

  await mockAuthenticatedSession(page);

  await mockBattleDetails(page, battlesById);

  await page.route("**/api/v1/battles", async (route) => {
    if (await handleCorsIfPreflight(route)) return;
    if (route.request().method() !== "POST") {
      await route.abort();
      return;
    }

    createCsrfHeaders.push(route.request().headers()["x-csrf-token"]);
    createCount += 1;
    if (createCount === 1) {
      await route.fulfill({
        status: 500,
        contentType: "application/json",
        headers: CORS_HEADERS,
        body: JSON.stringify({ detail: "No candidate model pair available" }),
      });
      return;
    }

    const battle = makeBattle("battle-bootstrap-retry", "Retry-path JP source");
    battlesById.set(battle.id, battle);

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      headers: CORS_HEADERS,
      body: JSON.stringify(battle),
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
        { event: "run.delta", data: { side: "A", text_delta: "Recovered stream A" } },
        { event: "run.delta", data: { side: "B", text_delta: "Recovered stream B" } },
        { event: "battle.completed", data: {} },
      ]),
    });
  });

  await page.goto("/battle/new");

  await expect(page.getByRole("heading", { name: "Unable to load battle" })).toBeVisible({
    timeout: 60_000,
  });
  await expect(
    page.getByText(/POST \/battles failed: 500 - No candidate model pair available/),
  ).toBeVisible();

  await page.goto("/battle/new?r=bootstrap-retry");

  await expect(page).toHaveURL(/\/battle\/battle-bootstrap-retry$/);
  await expect(page.getByText(/^complete$/i)).toBeVisible({
    timeout: 60_000,
  });
  await expect(page.getByText("Retry-path JP source")).toBeVisible();
  await expect(page.getByText("Recovered stream A")).toBeVisible();
  await expect(page.getByText("Recovered stream B")).toBeVisible();
  await expect(
    page.getByText(/POST \/battles failed: 500 - No candidate model pair available/),
  ).toHaveCount(0);

  expect(createCount).toBe(2);
  expect(createCsrfHeaders).toEqual(["playwright-csrf-token", "playwright-csrf-token"]);
});

test("marks status as error on run.error and keeps voting disabled", async ({ page }) => {
  const battlesById = new Map<string, BattlePublic>();

  await mockAuthenticatedSession(page);

  await mockBattleDetails(page, battlesById);

  await page.route("**/api/v1/battles", async (route) => {
    if (await handleCorsIfPreflight(route)) return;
    if (route.request().method() !== "POST") {
      await route.abort();
      return;
    }

    const battle = makeBattle("battle-run-error", "Run-error JP source");
    battlesById.set(battle.id, battle);

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      headers: CORS_HEADERS,
      body: JSON.stringify(battle),
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
  await expect(page.getByRole("button", { name: /Model A is better/i })).toHaveCount(0);
  await expect(page.getByLabel("Optional feedback")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Submit Vote" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Retry Battle" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Start another battle" })).toBeVisible();
});

test("surfaces stream transport failures and keeps voting disabled", async ({ page }) => {
  const battlesById = new Map<string, BattlePublic>();

  await mockAuthenticatedSession(page);
  await mockBattleDetails(page, battlesById);

  await page.route("**/api/v1/battles", async (route) => {
    if (await handleCorsIfPreflight(route)) return;
    if (route.request().method() !== "POST") {
      await route.abort();
      return;
    }

    const battle = makeBattle("battle-stream-disconnect", "Disconnect JP source");
    battlesById.set(battle.id, battle);

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      headers: CORS_HEADERS,
      body: JSON.stringify(battle),
    });
  });

  await page.route(/\/api\/v1\/battles\/[^/]+\/stream$/, async (route) => {
    await route.fulfill({
      status: 400,
      contentType: "application/json",
      headers: CORS_HEADERS,
      body: JSON.stringify({ detail: "stream disconnected" }),
    });
  });

  await page.goto("/battle/new");

  await expect(page.getByText(/^error$/i)).toBeVisible({
    timeout: 60_000,
  });
  await expect(
    page.getByText(/SSE failed|Failed to fetch|Battle stream failed|GET \/battles\/.+ failed: 500/i),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: /Model B is better/i })).toHaveCount(0);
  await expect(page.getByLabel("Optional feedback")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Submit Vote" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Retry Battle" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Start another battle" })).toBeVisible();
});
