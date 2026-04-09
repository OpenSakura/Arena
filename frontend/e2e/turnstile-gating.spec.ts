import { expect, test, type Page } from "@playwright/test";

type VoteCapture = {
  authHeader: string | undefined;
  payload: Record<string, unknown>;
};

type MockBattleRoutesOptions = {
  battleId: string;
  anonVoteTurnstileRequired: boolean;
  onVote: (vote: VoteCapture) => void;
};

async function mockBattleRoutes(page: Page, options: MockBattleRoutesOptions): Promise<void> {
  await page.route("**/api/v1/public-config", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        anon_vote_turnstile_required: options.anonVoteTurnstileRequired,
      }),
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

  await page.route(/\/api\/v1\/battles\/[^/]+\/vote$/, async (route) => {
    const payload = route.request().postDataJSON() as Record<string, unknown>;
    const winner =
      payload.winner === "A" || payload.winner === "B" || payload.winner === "tie"
        ? payload.winner
        : "A";

    options.onVote({
      authHeader: route.request().headers()["authorization"],
      payload,
    });

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        vote_id: "vote-1",
        battle_id: options.battleId,
        winner,
        reveal: {
          A: { model_id: "model-a", display_name: "Model A" },
          B: { model_id: "model-b", display_name: "Model B" },
        },
      }),
    });
  });
}

test("anonymous submit is blocked until Turnstile returns a token", async ({ page }) => {
  const votes: VoteCapture[] = [];

  await page.route("**/api/auth/session*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: "null",
    });
  });

  await page.addInitScript(() => {
    (window as any).turnstile = {
      render: (container: HTMLElement, options: Record<string, unknown>) => {
        const solveButton = document.createElement("button");
        solveButton.type = "button";
        solveButton.textContent = "Solve Turnstile";
        solveButton.addEventListener("click", () => {
          const callback = options.callback;
          if (typeof callback === "function") {
            callback("mock-turnstile-token");
          }
        });
        container.appendChild(solveButton);
        return "mock-turnstile-widget";
      },
      reset: () => {},
      remove: () => {},
    };
  });

  await page.route("**/turnstile/v0/api.js*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/javascript",
      body: "",
    });
  });

  await mockBattleRoutes(page, {
    battleId: "battle-turnstile-anon",
    anonVoteTurnstileRequired: true,
    onVote: (vote) => {
      votes.push(vote);
    },
  });

  await page.goto("/battle/new");

  await expect(page.getByText(/done/i)).toBeVisible({
    timeout: 60_000,
  });
  
  await page.getByRole("button", { name: /Model B is better/i }).click();

  const submit = page.getByRole("button", { name: "Submit Vote" });
  await expect(submit).toBeDisabled();

  await page.getByRole("button", { name: "Solve Turnstile" }).click();
  await expect(submit).toBeEnabled();

  await submit.click();
  await expect(page.getByText("Model A", { exact: true }).first()).toBeVisible();

  expect(votes).toHaveLength(1);
  expect(votes[0]?.payload).toMatchObject({
    winner: "B",
    turnstile_token: "mock-turnstile-token",
  });
});

test("authenticated users bypass Turnstile even when backend requires it", async ({ page }) => {
  const votes: VoteCapture[] = [];

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
    anonVoteTurnstileRequired: true,
    onVote: (vote) => {
      votes.push(vote);
    },
  });

  await page.goto("/battle/new");

  await expect(page.getByText(/done/i)).toBeVisible({
    timeout: 60_000,
  });
    await expect(page.getByRole("button", { name: "Solve Turnstile" })).toHaveCount(0);

  await page.getByRole("button", { name: /Model A is better/i }).click();
  const submit = page.getByRole("button", { name: "Submit Vote" });
  await expect(submit).toBeEnabled();
  await submit.click();

  await expect(page.getByText("Model A", { exact: true }).first()).toBeVisible();

  expect(votes).toHaveLength(1);
  expect(votes[0]?.authHeader).toBe("Bearer e2e-access-token");
  expect(votes[0]?.payload).toMatchObject({
    winner: "A",
    turnstile_token: null,
  });
});

test("requires Turnstile after an authenticated session expires", async ({ page }) => {
  const votes: VoteCapture[] = [];
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

  await page.addInitScript(() => {
    (window as any).turnstile = {
      render: (container: HTMLElement, options: Record<string, unknown>) => {
        const solveButton = document.createElement("button");
        solveButton.type = "button";
        solveButton.textContent = "Solve Turnstile";
        solveButton.addEventListener("click", () => {
          const callback = options.callback;
          if (typeof callback === "function") {
            callback("mock-turnstile-token");
          }
        });
        container.appendChild(solveButton);
        return "mock-turnstile-widget";
      },
      reset: () => {},
      remove: () => {},
    };
  });

  await page.route("**/turnstile/v0/api.js*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/javascript",
      body: "",
    });
  });

  await mockBattleRoutes(page, {
    battleId: "battle-turnstile-session-expired",
    anonVoteTurnstileRequired: true,
    onVote: (vote) => {
      votes.push(vote);
    },
  });

  await page.goto("/battle/new");

  await expect(page.getByText(/done/i)).toBeVisible({
    timeout: 60_000,
  });
    await expect(page.getByRole("button", { name: "Solve Turnstile" })).toHaveCount(0);

  await page.getByRole("button", { name: /Model A is better/i }).click();
  const submitBeforeExpiry = page.getByRole("button", { name: "Submit Vote" });
  await expect(submitBeforeExpiry).toBeEnabled();

  isAuthenticated = false;
  await page.reload();

  await expect(page.getByText(/done/i)).toBeVisible({
    timeout: 60_000,
  });
  
  await page.getByRole("button", { name: /Model A is better/i }).click();
  const submitAfterExpiry = page.getByRole("button", { name: "Submit Vote" });
  await expect(submitAfterExpiry).toBeDisabled();

  await page.getByRole("button", { name: "Solve Turnstile" }).click();
  await expect(submitAfterExpiry).toBeEnabled();

  await submitAfterExpiry.click();
  await expect(page.getByText("Model A", { exact: true }).first()).toBeVisible();

  expect(votes).toHaveLength(1);
  expect(votes[0]?.authHeader).toBeUndefined();
  expect(votes[0]?.payload).toMatchObject({
    winner: "A",
    turnstile_token: "mock-turnstile-token",
  });
});

test("anonymous submit re-locks after Turnstile token expires", async ({ page }) => {
  const votes: VoteCapture[] = [];

  await page.route("**/api/auth/session*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: "null",
    });
  });

  await page.addInitScript(() => {
    (window as any).turnstile = {
      render: (container: HTMLElement, options: Record<string, unknown>) => {
        const solveButton = document.createElement("button");
        solveButton.type = "button";
        solveButton.textContent = "Solve Turnstile";
        solveButton.addEventListener("click", () => {
          const callback = options.callback;
          if (typeof callback === "function") {
            callback("mock-turnstile-token");
          }
        });

        const expireButton = document.createElement("button");
        expireButton.type = "button";
        expireButton.textContent = "Expire Turnstile";
        expireButton.addEventListener("click", () => {
          const onExpire = options["expired-callback"];
          if (typeof onExpire === "function") {
            onExpire();
          }
        });

        container.appendChild(solveButton);
        container.appendChild(expireButton);
        return "mock-turnstile-widget";
      },
      reset: () => {},
      remove: () => {},
    };
  });

  await page.route("**/turnstile/v0/api.js*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/javascript",
      body: "",
    });
  });

  await mockBattleRoutes(page, {
    battleId: "battle-turnstile-expire",
    anonVoteTurnstileRequired: true,
    onVote: (vote) => {
      votes.push(vote);
    },
  });

  await page.goto("/battle/new");

  await expect(page.getByText(/done/i)).toBeVisible({
    timeout: 60_000,
  });

  await page.getByRole("button", { name: /Model A is better/i }).click();

  const submit = page.getByRole("button", { name: "Submit Vote" });
  await expect(submit).toBeDisabled();

  await page.getByRole("button", { name: "Solve Turnstile" }).click();
  await expect(submit).toBeEnabled();

  await page.getByRole("button", { name: "Expire Turnstile" }).click();
  await expect(submit).toBeDisabled();

  await page.getByRole("button", { name: "Solve Turnstile" }).click();
  await expect(submit).toBeEnabled();

  await submit.click();
  await expect(page.getByText("Model A", { exact: true }).first()).toBeVisible();

  expect(votes).toHaveLength(1);
  expect(votes[0]?.payload).toMatchObject({
    winner: "A",
    turnstile_token: "mock-turnstile-token",
  });
});

test("keeps submit disabled and shows error when Turnstile widget errors", async ({ page }) => {
  await page.route("**/api/auth/session*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: "null",
    });
  });

  await page.addInitScript(() => {
    (window as any).turnstile = {
      render: (container: HTMLElement, options: Record<string, unknown>) => {
        const errorButton = document.createElement("button");
        errorButton.type = "button";
        errorButton.textContent = "Trigger Turnstile Error";
        errorButton.addEventListener("click", () => {
          const onError = options["error-callback"];
          if (typeof onError === "function") {
            onError();
          }
        });

        container.appendChild(errorButton);
        return "mock-turnstile-widget";
      },
      reset: () => {},
      remove: () => {},
    };
  });

  await page.route("**/turnstile/v0/api.js*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/javascript",
      body: "",
    });
  });

  await mockBattleRoutes(page, {
    battleId: "battle-turnstile-error",
    anonVoteTurnstileRequired: true,
    onVote: () => {
      throw new Error("Vote endpoint should not be called when Turnstile fails");
    },
  });

  await page.goto("/battle/new");

  await expect(page.getByText(/done/i)).toBeVisible({
    timeout: 60_000,
  });

  await page.getByRole("button", { name: /Model B is better/i }).click();
  const submit = page.getByRole("button", { name: "Submit Vote" });
  await expect(submit).toBeDisabled();

  await page.getByRole("button", { name: "Trigger Turnstile Error" }).click();

  await expect(page.getByText(/^Turnstile error$/)).toBeVisible();
  await expect(submit).toBeDisabled();
  await expect(page.getByText("Reveal")).toHaveCount(0);
});
