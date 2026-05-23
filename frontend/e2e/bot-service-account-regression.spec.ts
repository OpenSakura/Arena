import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs/promises";
import path from "node:path";

import { mockSpaAuthenticatedSession } from "./spa-auth";

const SERVICE_SCOPES = [
  "battle:create",
  "battle:read",
  "battle:execute",
  "vote:create",
] as const;
const TASK_12_FAKE_TOKEN = "osa_bot_example_task12_plaintext_token";

type TokenRecord = {
  id: string;
  service_account_id: string;
  token_prefix: string;
  status: "active" | "revoked";
  scopes: string[];
  created_at: string;
  expires_at: string | null;
  last_used_at: string | null;
  revoked_at: string | null;
};

type AccountRecord = {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
  scopes: string[];
  tokens: TokenRecord[];
  created_at: string;
  updated_at: string;
};

type TokenPayload = {
  scopes?: string[];
  expires_at?: string | null;
};

async function setupAdminServiceAccountRoutes(page: Page): Promise<{
  createTokenPayloads: TokenPayload[];
  revokeTokenIds: string[];
}> {
  const createTokenPayloads: TokenPayload[] = [];
  const revokeTokenIds: string[] = [];
  let accounts: AccountRecord[] = [
    {
      id: "sa-task-12",
      name: "Task 12 Judge Bot",
      description: "Combined UI regression account",
      enabled: true,
      scopes: [],
      tokens: [],
      created_at: "2026-05-23T00:00:00Z",
      updated_at: "2026-05-23T00:00:00Z",
    },
  ];

  await page.route("**/api/v1/admin/service-accounts", async (route) => {
    if (route.request().method() !== "GET") {
      await route.abort();
      return;
    }

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ service_accounts: accounts }),
    });
  });

  await page.route(
    /\/api\/v1\/admin\/service-accounts\/[^/]+\/tokens$/,
    async (route) => {
      if (route.request().method() !== "POST") {
        await route.abort();
        return;
      }

      const payload = route.request().postDataJSON() as TokenPayload;
      createTokenPayloads.push(payload);
      const { pathname } = new URL(route.request().url());
      const parts = pathname.split("/");
      const serviceAccountId = parts[parts.length - 2] ?? "";
      const token: TokenRecord = {
        id: "tok-task-12",
        service_account_id: serviceAccountId,
        token_prefix: "osa_bot_example",
        status: "active",
        scopes: payload.scopes ?? [],
        created_at: "2026-05-23T00:00:00Z",
        expires_at: null,
        last_used_at: null,
        revoked_at: null,
      };

      accounts = accounts.map((account) =>
        account.id === serviceAccountId
          ? { ...account, scopes: token.scopes, tokens: [token, ...account.tokens] }
          : account,
      );

      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({
          service_account: accounts.find((account) => account.id === serviceAccountId),
          token,
          plaintext_token: TASK_12_FAKE_TOKEN,
        }),
      });
    },
  );

  await page.route(
    /\/api\/v1\/admin\/service-account-tokens\/[^/]+\/revoke$/,
    async (route) => {
      if (route.request().method() !== "POST") {
        await route.abort();
        return;
      }

      const { pathname } = new URL(route.request().url());
      const parts = pathname.split("/");
      const tokenId = parts[parts.length - 2] ?? "";
      revokeTokenIds.push(tokenId);
      accounts = accounts.map((account) => ({
        ...account,
        tokens: account.tokens.map((token) =>
          token.id === tokenId
            ? { ...token, status: "revoked", revoked_at: "2026-05-23T00:01:00Z" }
            : token,
        ),
      }));

      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ token_id: tokenId, revoked: true }),
      });
    },
  );

  return { createTokenPayloads, revokeTokenIds };
}

async function setupLeaderboardRoutes(
  page: Page,
): Promise<Array<{ url: string; judge_type: string }>> {
  const requests: Array<{ url: string; judge_type: string }> = [];
  const counts = {
    all: { human: 2, bot: 1, total: 3 },
    human: { human: 2, bot: 0, total: 2 },
    bot: { human: 0, bot: 1, total: 1 },
  } as const;

  await page.route(/\/api\/v1\/leaderboard(?:\?.*)?$/, async (route) => {
    const url = new URL(route.request().url());
    const judgeTypeParam = url.searchParams.get("judge_type");
    const judgeType =
      judgeTypeParam === "human" || judgeTypeParam === "bot"
        ? judgeTypeParam
        : "all";
    requests.push({ url: url.toString(), judge_type: judgeType });

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        method: url.searchParams.get("method") === "bt" ? "bt" : "elo",
        ci: false,
        bootstrap_rounds: null,
        vote_source_counts: counts[judgeType],
        models: [
          {
            model_id: `model-task-12-${judgeType}`,
            display_name: `Task 12 ${judgeType} Model`,
            rating: judgeType === "bot" ? 1510 : 1500,
            rating_lower: null,
            rating_upper: null,
            games_played: counts[judgeType].total,
          },
        ],
      }),
    });
  });

  return requests;
}

test("integrated bot admin token and leaderboard filters regression", async ({ page }) => {
  await mockSpaAuthenticatedSession(page, {
    accessToken: "task-12-admin-access-token",
    isAdmin: true,
  });
  const adminRoutes = await setupAdminServiceAccountRoutes(page);
  const leaderboardRequests = await setupLeaderboardRoutes(page);

  await page.goto("/admin/service-accounts");
  await expect(page.getByText("Task 12 Judge Bot")).toBeVisible();
  await page.getByRole("button", { name: "Tokens" }).click();
  await page.getByRole("button", { name: "New Token" }).click();

  for (const scope of SERVICE_SCOPES) {
    await page.locator("label").filter({ hasText: scope }).locator("input").check();
  }
  await page.getByRole("button", { name: "Confirm Create" }).click();

  await expect(
    page.getByText("Copy now. This token will not be shown again."),
  ).toBeVisible();
  await expect(page.getByText(TASK_12_FAKE_TOKEN)).toBeVisible();
  expect(adminRoutes.createTokenPayloads).toHaveLength(1);
  expect(adminRoutes.createTokenPayloads[0]?.scopes).toEqual([...SERVICE_SCOPES]);

  const storageAudit = await page.evaluate((token) => ({
    localStorageHasToken: JSON.stringify(window.localStorage).includes(token),
    sessionStorageHasToken: JSON.stringify(window.sessionStorage).includes(token),
  }), TASK_12_FAKE_TOKEN);
  expect(storageAudit.localStorageHasToken).toBe(false);
  expect(storageAudit.sessionStorageHasToken).toBe(false);

  await page.getByRole("button", { name: "Dismiss" }).click();
  await expect(page.getByText(TASK_12_FAKE_TOKEN)).not.toBeVisible();

  page.once("dialog", async (dialog) => {
    await dialog.accept();
  });
  await page.getByRole("button", { name: "Revoke" }).click();
  await expect(page.getByText(/^revoked/)).toBeVisible();
  expect(adminRoutes.revokeTokenIds).toEqual(["tok-task-12"]);

  await page.goto("/leaderboard");
  await expect(page.getByRole("link", { name: "All votes" })).toBeVisible();
  await expect(page.getByText("3 total")).toBeVisible();

  await page.getByRole("link", { name: "Human votes" }).click();
  await expect(page).toHaveURL(/\/leaderboard\?.*judge_type=human/);
  await expect(page.getByText("2 total")).toBeVisible();

  await page.getByRole("link", { name: "Bot votes" }).click();
  await expect(page).toHaveURL(/\/leaderboard\?.*judge_type=bot/);
  await expect(page.getByText("1 total")).toBeVisible();

  const seenJudgeTypes = [
    ...new Set(leaderboardRequests.map((request) => request.judge_type)),
  ].sort();
  expect(seenJudgeTypes).toEqual(["all", "bot", "human"]);

  const evidenceDir = path.resolve(__dirname, "../../.omo/evidence");
  await fs.mkdir(evidenceDir, { recursive: true });
  await page.screenshot({
    path: path.join(evidenceDir, "task-12-ui-regression.png"),
    fullPage: true,
  });
});
