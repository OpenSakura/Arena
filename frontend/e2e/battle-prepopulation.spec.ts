import { expect, test, type Page, type Route } from "@playwright/test";

import { auditBrowserAuthLeakage, expectNoAuthorizationHeaders } from "./browser-leakage";
import { mockSpaAuthenticatedSession } from "./spa-auth";

type Side = "A" | "B";

type AdminModelOption = {
  id: string;
  display_name: string;
};

type AdminStats = {
  available_admin_count: number;
  available_recycled_count: number;
  available_total_count: number;
  generating_count: number;
  failed_count: number;
  voted_consumed_count: number;
  total_count: number;
  max_job_size: number;
  latest_job: AdminJob | null;
};

type AdminJob = {
  id: string;
  status: string;
  requested_count: number;
  completed_count: number;
  failed_count: number;
  model_ids: string[];
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  last_error: string | null;
};

type BattlePrepopulationMetadata = {
  source: "admin_pre_generated" | "user_recycled" | "live";
  pooled: boolean;
  display_delay_ms: number | null;
  backend_gated_replay: boolean;
};

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
  prepopulation?: BattlePrepopulationMetadata | null;
};

const POOLED_DISPLAY_DELAY_MS = 10_000;

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

async function mockAdminSession(page: Page): Promise<void> {
  await mockSpaAuthenticatedSession(page, {
    isAdmin: true,
    profile: { sub: "admin-prepopulation-e2e", name: "Prepopulation Admin" },
  });
}

async function mockAuthenticatedBattleUser(page: Page): Promise<void> {
  await mockSpaAuthenticatedSession(page, {
    profile: {
      sub: "battle-prepopulation-e2e-user",
      name: "Battle Prepopulation E2E",
      email: "battle-prepopulation-e2e@example.com",
    },
  });
}

function statsRecord(overrides: Partial<AdminStats> = {}): AdminStats {
  return {
    available_admin_count: 4,
    available_recycled_count: 2,
    available_total_count: 6,
    generating_count: 1,
    failed_count: 0,
    voted_consumed_count: 3,
    total_count: 10,
    max_job_size: 50,
    latest_job: null,
    ...overrides,
  };
}

function jobRecord(overrides: Partial<AdminJob> = {}): AdminJob {
  return {
    id: "job-1",
    status: "queued",
    requested_count: 3,
    completed_count: 0,
    failed_count: 0,
    model_ids: [],
    created_at: "2026-05-27T00:00:00.000Z",
    started_at: null,
    finished_at: null,
    last_error: null,
    ...overrides,
  };
}

function makeBattle({
  id,
  sourceText,
  status = "pending",
  outputA = null,
  outputB = null,
  prepopulation = null,
}: {
  id: string;
  sourceText: string;
  status?: string;
  outputA?: string | null;
  outputB?: string | null;
  prepopulation?: BattlePrepopulationMetadata | null;
}): BattlePublic {
  return {
    id,
    task_id: `task-${id}`,
    source_text: sourceText,
    source_lang: "ja",
    target_lang: "zh",
    mode: "jp2zh_ab",
    status,
    retry_allowed: false,
    run_a: {
      id: `${id}-run-a`,
      side: "A",
      output_text: outputA,
      stats: null,
      error_text: null,
    },
    run_b: {
      id: `${id}-run-b`,
      side: "B",
      output_text: outputB,
      stats: null,
      error_text: null,
    },
    prepopulation,
  };
}

function sseBody(events: Array<{ event: string; data: unknown }>): string {
  return events
    .map(({ event, data }) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)
    .join("");
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

test("admin battle prepopulation submits zero, one, and two selected models without auth leakage", async ({ page }, testInfo) => {
  const models: AdminModelOption[] = [
    { id: "model-alpha", display_name: "Model Alpha" },
    { id: "model-bravo", display_name: "Model Bravo" },
  ];
  const adminRequests: Array<{ method: string; path: string; authorizationHeader: string | undefined }> = [];
  const createJobCalls: Array<{
    authorizationHeader: string | undefined;
    csrfHeader: string | undefined;
    payload: Record<string, unknown>;
  }> = [];
  let stats = statsRecord();
  let jobs: AdminJob[] = [];

  await mockAdminSession(page);

  await page.route(/\/api\/v1\/admin\/battle-prepopulation\/stats$/, async (route) => {
    adminRequests.push({
      method: route.request().method(),
      path: new URL(route.request().url()).pathname,
      authorizationHeader: route.request().headers()["authorization"],
    });

    if (route.request().method() !== "GET") {
      await route.abort();
      return;
    }

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(stats),
    });
  });

  await page.route(/\/api\/v1\/admin\/battle-prepopulation\/model-options$/, async (route) => {
    adminRequests.push({
      method: route.request().method(),
      path: new URL(route.request().url()).pathname,
      authorizationHeader: route.request().headers()["authorization"],
    });

    if (route.request().method() !== "GET") {
      await route.abort();
      return;
    }

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ models }),
    });
  });

  await page.route(/\/api\/v1\/admin\/battle-prepopulation\/jobs(?:\?.*)?$/, async (route) => {
    adminRequests.push({
      method: route.request().method(),
      path: new URL(route.request().url()).pathname,
      authorizationHeader: route.request().headers()["authorization"],
    });

    if (route.request().method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ jobs }),
      });
      return;
    }

    if (route.request().method() === "POST") {
      const payload = route.request().postDataJSON() as Record<string, unknown>;
      createJobCalls.push({
        authorizationHeader: route.request().headers()["authorization"],
        csrfHeader: route.request().headers()["x-csrf-token"],
        payload,
      });

      const created = jobRecord({
        id: `job-${createJobCalls.length}`,
        requested_count: Number(payload.amount),
        model_ids: Array.isArray(payload.model_ids) ? payload.model_ids.map(String) : [],
      });
      jobs = [created, ...jobs];
      stats = statsRecord({
        available_admin_count: stats.available_admin_count + created.requested_count,
        available_total_count: stats.available_total_count + created.requested_count,
        total_count: stats.total_count + created.requested_count,
        latest_job: created,
      });

      await route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify(created),
      });
      return;
    }

    await route.abort();
  });

  await page.goto("/admin/battle-prepopulation");

  await expect(page.getByRole("heading", { name: "Battle Prepopulation" })).toBeVisible();
  await expect(page.getByText("Available admin battles: 4")).toBeVisible();
  await expect(page.getByText("Total available: 6")).toBeVisible();
  await expect(page.getByText("Voted and consumed: 3")).toBeVisible();
  await expect(page.getByText("Max job size: 50")).toBeVisible();
  await expect(page.getByRole("option", { name: "Model Alpha" })).toHaveCount(2);
  await expect(page.getByRole("option", { name: "Model Bravo" })).toHaveCount(2);

  const amountInput = page.getByLabel("Battles to generate");
  const model1Select = page.getByLabel("Model 1");
  const model2Select = page.getByLabel("Model 2");
  const submitButton = page.getByRole("button", { name: "Prepopulate battles" });

  await amountInput.fill("3");
  await submitButton.click();
  await expect.poll(() => createJobCalls.length).toBe(1);
  await expect(amountInput).toHaveValue("");

  await amountInput.fill("3");
  await model1Select.selectOption("model-alpha");
  await submitButton.click();
  await expect.poll(() => createJobCalls.length).toBe(2);
  await expect(amountInput).toHaveValue("");

  await amountInput.fill("3");
  await model1Select.selectOption("model-alpha");
  await model2Select.selectOption("model-bravo");
  await submitButton.click();
  await expect.poll(() => createJobCalls.length).toBe(3);
  await expect(page.getByText("job-3")).toBeVisible();

  expect(createJobCalls.map((call) => call.payload)).toEqual([
    { amount: 3, model_ids: [] },
    { amount: 3, model_ids: ["model-alpha"] },
    { amount: 3, model_ids: ["model-alpha", "model-bravo"] },
  ]);
  expect(createJobCalls.map((call) => call.csrfHeader)).toEqual([
    "playwright-csrf-token",
    "playwright-csrf-token",
    "playwright-csrf-token",
  ]);
  expect(createJobCalls.map((call) => call.authorizationHeader)).toEqual([
    undefined,
    undefined,
    undefined,
  ]);
  expect(adminRequests.filter((request) => request.authorizationHeader)).toEqual([]);
  expectNoAuthorizationHeaders(page);
  await auditBrowserAuthLeakage(page, "admin-battle-prepopulation-flow", testInfo);
});

test("pooled admin pre-generated battle shows source immediately and streams output from backend", async ({ page }, testInfo) => {
  const battlesById = new Map<string, BattlePublic>();
  const createBattleCalls: Array<{
    authorizationHeader: string | undefined;
    csrfHeader: string | undefined;
    payload: Record<string, unknown>;
  }> = [];
  let streamCalls = 0;
  let releaseStream!: () => void;
  const streamRelease = new Promise<void>((resolve) => {
    releaseStream = resolve;
  });
  const pooledOutputA = `${"Pooled translation output A with enough replay text to observe partial A. ".repeat(80)}A finale.`;
  const pooledOutputB = `${"Pooled translation output B with enough replay text to observe partial B. ".repeat(80)}B finale.`;
  const pooledBattle = makeBattle({
    id: "battle-pooled-admin",
    sourceText: "Pooled admin source text",
    status: "completed",
    outputA: null,
    outputB: null,
    prepopulation: {
      source: "admin_pre_generated",
      pooled: true,
      display_delay_ms: POOLED_DISPLAY_DELAY_MS,
      backend_gated_replay: true,
    },
  });
  battlesById.set(pooledBattle.id, pooledBattle);

  await mockAuthenticatedBattleUser(page);

  await page.route(/\/api\/v1\/battles(?:\?.*)?$/, async (route) => {
    if (await handleCorsIfPreflight(route)) return;
    if (route.request().method() !== "POST") {
      await route.abort();
      return;
    }

    createBattleCalls.push({
      authorizationHeader: route.request().headers()["authorization"],
      csrfHeader: route.request().headers()["x-csrf-token"],
      payload: route.request().postDataJSON() as Record<string, unknown>,
    });

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      headers: CORS_HEADERS,
      body: JSON.stringify(pooledBattle),
    });
  });

  await mockBattleDetails(page, battlesById);

  await page.route(/\/api\/v1\/battles\/[^/]+\/stream$/, async (route) => {
    if (await handleCorsIfPreflight(route)) return;
    streamCalls += 1;
    await streamRelease;
    await route.fulfill({
      status: 200,
      headers: {
        "content-type": "text/event-stream; charset=utf-8",
        "cache-control": "no-cache",
        ...CORS_HEADERS,
      },
      body: sseBody([
        { event: "run.delta", data: { side: "A", text_delta: pooledOutputA.slice(0, 40), replay: true, chunk_index: 0 } },
        { event: "run.delta", data: { side: "B", text_delta: pooledOutputB.slice(0, 40), replay: true, chunk_index: 0 } },
        { event: "run.delta", data: { side: "A", text_delta: pooledOutputA.slice(40), replay: true, chunk_index: 1 } },
        { event: "run.delta", data: { side: "B", text_delta: pooledOutputB.slice(40), replay: true, chunk_index: 1 } },
        { event: "battle.completed", data: { battle_id: "battle-pooled-admin", replay: true } },
      ]),
    });
  });

  const createResponse = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname === "/api/v1/battles" && response.request().method() === "POST";
  });

  await page.goto("/battle/new");
  await createResponse;

  await expect(page).toHaveURL(/\/battle\/battle-pooled-admin$/);
  await expect(page.getByText("Pooled admin source text")).toBeVisible();
  await expect(page.getByText(pooledOutputA)).toHaveCount(0, { timeout: 0 });
  await expect(page.getByText(pooledOutputB)).toHaveCount(0, { timeout: 0 });
  await expect(page.getByRole("button", { name: /Model A is better/i })).toHaveCount(0, { timeout: 0 });
  await expect(page.getByRole("button", { name: "Submit Vote" })).toHaveCount(0);
  await expect.poll(() => streamCalls).toBe(1);

  releaseStream();

  await expect(page.getByText(pooledOutputA)).toBeVisible();
  await expect(page.getByText(pooledOutputB)).toBeVisible();
  const panelTextAfterReplay = await page.locator("body").innerText();
  expect(panelTextAfterReplay).toContain(pooledOutputA);
  expect(panelTextAfterReplay).toContain(pooledOutputB);
  await expect(page.getByRole("button", { name: /Model A is better/i })).toBeVisible();
  await expect(page.getByRole("button", { name: /Model B is better/i })).toBeVisible();
  await expect(page.getByRole("button", { name: "Submit Vote" })).toBeDisabled();

  await page.getByRole("button", { name: /Model B is better/i }).click();
  await expect(page.getByRole("button", { name: "Submit Vote" })).toBeEnabled();

  expect(createBattleCalls).toHaveLength(1);
  expect(createBattleCalls[0]?.payload).toEqual({});
  expect(createBattleCalls[0]?.csrfHeader).toBe("playwright-csrf-token");
  expect(createBattleCalls[0]?.authorizationHeader).toBeUndefined();
  expect(streamCalls).toBe(1);
  expectNoAuthorizationHeaders(page);
  await auditBrowserAuthLeakage(page, "pooled-battle-display-delay-flow", testInfo);
});

test("live fallback pending battle streams immediately and second vote conflict is visible", async ({ page }, testInfo) => {
  const battlesById = new Map<string, BattlePublic>();
  const createBattleCalls: Array<{
    authorizationHeader: string | undefined;
    csrfHeader: string | undefined;
    payload: Record<string, unknown>;
  }> = [];
  const voteCalls: Array<{
    authorizationHeader: string | undefined;
    csrfHeader: string | undefined;
    payload: Record<string, unknown>;
  }> = [];
  let streamCalls = 0;
  const liveBattle = makeBattle({
    id: "battle-live-fallback",
    sourceText: "Live fallback source text",
  });
  battlesById.set(liveBattle.id, liveBattle);

  await mockAuthenticatedBattleUser(page);

  await page.route(/\/api\/v1\/battles(?:\?.*)?$/, async (route) => {
    if (await handleCorsIfPreflight(route)) return;
    if (route.request().method() !== "POST") {
      await route.abort();
      return;
    }

    createBattleCalls.push({
      authorizationHeader: route.request().headers()["authorization"],
      csrfHeader: route.request().headers()["x-csrf-token"],
      payload: route.request().postDataJSON() as Record<string, unknown>,
    });

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      headers: CORS_HEADERS,
      body: JSON.stringify(liveBattle),
    });
  });

  await mockBattleDetails(page, battlesById);

  await page.route(/\/api\/v1\/battles\/[^/]+\/stream$/, async (route) => {
    if (await handleCorsIfPreflight(route)) return;
    streamCalls += 1;
    await route.fulfill({
      status: 200,
      headers: {
        "content-type": "text/event-stream; charset=utf-8",
        "cache-control": "no-cache",
        ...CORS_HEADERS,
      },
      body: sseBody([
        { event: "run.delta", data: { side: "A", text_delta: "Live fallback output A" } },
        { event: "run.delta", data: { side: "B", text_delta: "Live fallback output B" } },
        { event: "battle.completed", data: {} },
      ]),
    });
  });

  await page.route(/\/api\/v1\/battles\/[^/]+\/vote$/, async (route) => {
    if (await handleCorsIfPreflight(route)) return;
    voteCalls.push({
      authorizationHeader: route.request().headers()["authorization"],
      csrfHeader: route.request().headers()["x-csrf-token"],
      payload: route.request().postDataJSON() as Record<string, unknown>,
    });

    await route.fulfill({
      status: 409,
      contentType: "application/json",
      headers: CORS_HEADERS,
      body: JSON.stringify({ detail: "Battle already has a vote" }),
    });
  });

  await page.goto("/battle/new");

  await expect(page).toHaveURL(/\/battle\/battle-live-fallback$/);
  await expect(page.getByText("Live fallback source text")).toBeVisible();
  await expect(page.getByText("Live fallback output A")).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText("Live fallback output B")).toBeVisible();
  await expect(page.getByRole("button", { name: /Model A is better/i })).toBeVisible();
  await expect(page.getByRole("button", { name: "Submit Vote" })).toBeDisabled();

  await page.getByRole("button", { name: /Model A is better/i }).click();
  const submitVote = page.getByRole("button", { name: "Submit Vote" });
  await expect(submitVote).toBeEnabled();
  await submitVote.click();

  await expect(page.getByText(/Battle already has a vote/)).toBeVisible();
  await expect(page.getByText("Models Revealed")).toHaveCount(0);
  await expect(submitVote).toBeEnabled();

  expect(createBattleCalls).toHaveLength(1);
  expect(createBattleCalls[0]?.payload).toEqual({});
  expect(createBattleCalls[0]?.csrfHeader).toBe("playwright-csrf-token");
  expect(createBattleCalls[0]?.authorizationHeader).toBeUndefined();
  expect(streamCalls).toBeGreaterThanOrEqual(1);
  expect(voteCalls).toHaveLength(1);
  expect(voteCalls[0]?.payload).toMatchObject({
    winner: "A",
    comment: null,
  });
  expect(voteCalls[0]?.csrfHeader).toBe("playwright-csrf-token");
  expect(voteCalls[0]?.authorizationHeader).toBeUndefined();
  expectNoAuthorizationHeaders(page);
  await auditBrowserAuthLeakage(page, "live-fallback-vote-conflict-flow", testInfo);
});
