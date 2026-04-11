import { expect, test, type Page } from "@playwright/test";

type BattleCreateCapture = {
  authHeader: string | undefined;
  payload: Record<string, unknown>;
};

type MockBattleRoutesOptions = {
  battleId: string;
  anonBattleTurnstileRequired: boolean;
  onCreate: (create: BattleCreateCapture) => void;
};

async function mockBattleRoutes(
  page: Page,
  options: MockBattleRoutesOptions,
): Promise<void> {
  await page.route("**/api/v1/public-config", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        anon_battle_turnstile_required: options.anonBattleTurnstileRequired,
      }),
    });
  });

  await page.route("**/api/v1/battles", async (route) => {
    if (route.request().method() !== "POST") {
      await route.abort();
      return;
    }

    const payload = route.request().postDataJSON() as Record<string, unknown>;
    options.onCreate({
      authHeader: route.request().headers()["authorization"],
      payload,
    });

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: options.battleId,
        task_id: `task-${options.battleId}`,
        source_text: "Turnstile source",
        source_lang: "ja",
        target_lang: "zh",
        mode: "jp2zh_ab",
        status: "completed",
        run_a: {
          id: `${options.battleId}-run-a`,
          side: "A",
          output_text: "Output A",
          stats: null,
          error_text: null,
        },
        run_b: {
          id: `${options.battleId}-run-b`,
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
}

async function mockTurnstileScript(
  page: Page,
  options: { includeExpireButton?: boolean; includeErrorButton?: boolean } = {},
): Promise<void> {
  await page.addInitScript((scriptOptions) => {
    (window as any).turnstile = {
      render: (container: HTMLElement, callbackOptions: Record<string, unknown>) => {
        const solveButton = document.createElement("button");
        solveButton.type = "button";
        solveButton.textContent = "Solve Turnstile";
        solveButton.addEventListener("click", () => {
          const callback = callbackOptions.callback;
          if (typeof callback === "function") {
            callback("mock-turnstile-token");
          }
        });
        container.appendChild(solveButton);

        if (scriptOptions.includeExpireButton) {
          const expireButton = document.createElement("button");
          expireButton.type = "button";
          expireButton.textContent = "Expire Turnstile";
          expireButton.addEventListener("click", () => {
            const onExpire = callbackOptions["expired-callback"];
            if (typeof onExpire === "function") {
              onExpire();
            }
          });
          container.appendChild(expireButton);
        }

        if (scriptOptions.includeErrorButton) {
          const errorButton = document.createElement("button");
          errorButton.type = "button";
          errorButton.textContent = "Trigger Turnstile Error";
          errorButton.addEventListener("click", () => {
            const onError = callbackOptions["error-callback"];
            if (typeof onError === "function") {
              onError();
            }
          });
          container.appendChild(errorButton);
        }

        return "mock-turnstile-widget";
      },
      reset: () => {},
      remove: () => {},
    };
  }, options);

  await page.route("**/turnstile/v0/api.js*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/javascript",
      body: "",
    });
  });
}

test("anonymous battle creation waits for Turnstile before POSTing", async ({ page }) => {
  const creates: BattleCreateCapture[] = [];

  await page.route("**/api/auth/session*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: "null",
    });
  });

  await mockTurnstileScript(page);
  await mockBattleRoutes(page, {
    battleId: "battle-turnstile-anon",
    anonBattleTurnstileRequired: true,
    onCreate: (create) => {
      creates.push(create);
    },
  });

  await page.goto("/battle/new");

  await expect(page.getByText("Verification Required")).toBeVisible({
    timeout: 60_000,
  });
  expect(creates).toHaveLength(0);

  await page.getByRole("button", { name: "Solve Turnstile" }).click();

  await expect(page.getByText(/done/i)).toBeVisible({ timeout: 60_000 });
  expect(creates).toHaveLength(1);
  expect(creates[0]?.payload).toMatchObject({
    turnstile_token: "mock-turnstile-token",
  });
});

test("authenticated users bypass Turnstile for battle creation", async ({ page }) => {
  const creates: BattleCreateCapture[] = [];

  await page.route("**/api/auth/session*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        user: { name: "Arena E2E", email: "arena-e2e@example.com" },
        expires: "2099-01-01T00:00:00.000Z",
        accessToken: "e2e-access-token",
      }),
    });
  });

  await mockBattleRoutes(page, {
    battleId: "battle-turnstile-authed",
    anonBattleTurnstileRequired: true,
    onCreate: (create) => {
      creates.push(create);
    },
  });

  await page.goto("/battle/new");

  await expect(page.getByText(/done/i)).toBeVisible({ timeout: 60_000 });
  await expect(page.getByRole("button", { name: "Solve Turnstile" })).toHaveCount(0);

  expect(creates).toHaveLength(1);
  expect(creates[0]?.authHeader).toBe("Bearer e2e-access-token");
  expect(creates[0]?.payload).toEqual({});
});

test("a new anonymous battle is gated after an authenticated session expires", async ({ page }) => {
  const creates: BattleCreateCapture[] = [];
  let isAuthenticated = true;

  await page.route("**/api/auth/session*", async (route) => {
    if (isAuthenticated) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          user: { name: "Arena E2E", email: "arena-e2e@example.com" },
          expires: "2099-01-01T00:00:00.000Z",
          accessToken: "e2e-access-token",
        }),
      });
      return;
    }

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: "null",
    });
  });

  await mockTurnstileScript(page);
  await mockBattleRoutes(page, {
    battleId: "battle-turnstile-session-expired",
    anonBattleTurnstileRequired: true,
    onCreate: (create) => {
      creates.push(create);
    },
  });

  await page.goto("/battle/new");
  await expect(page.getByText(/done/i)).toBeVisible({ timeout: 60_000 });
  expect(creates).toHaveLength(1);
  expect(creates[0]?.authHeader).toBe("Bearer e2e-access-token");

  isAuthenticated = false;
  await page.goto("/battle/new?expired=1");

  await expect(page.getByText("Verification Required")).toBeVisible({
    timeout: 60_000,
  });
  expect(creates).toHaveLength(1);

  await page.getByRole("button", { name: "Solve Turnstile" }).click();

  await expect(page.getByText(/done/i)).toBeVisible({ timeout: 60_000 });
  expect(creates).toHaveLength(2);
  expect(creates[1]?.authHeader).toBeUndefined();
  expect(creates[1]?.payload).toMatchObject({
    turnstile_token: "mock-turnstile-token",
  });
});

test("battle creation stays blocked and shows an error when Turnstile fails", async ({ page }) => {
  const creates: BattleCreateCapture[] = [];

  await page.route("**/api/auth/session*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: "null",
    });
  });

  await mockTurnstileScript(page, { includeErrorButton: true });
  await mockBattleRoutes(page, {
    battleId: "battle-turnstile-error",
    anonBattleTurnstileRequired: true,
    onCreate: (create) => {
      creates.push(create);
    },
  });

  await page.goto("/battle/new");

  await expect(page.getByText("Verification Required")).toBeVisible({
    timeout: 60_000,
  });
  await page.getByRole("button", { name: "Trigger Turnstile Error" }).click();

  await expect(page.getByText(/^Turnstile error$/)).toBeVisible();
  expect(creates).toHaveLength(0);
  await expect(page.getByRole("button", { name: "Solve Turnstile" })).toBeVisible();
});
