import { expect, test, type Page } from "@playwright/test";

type AdminModel = {
  id: string;
  display_name: string;
  provider_type: string;
  model_name: string;
  base_url: string;
  enabled: boolean;
  visibility: string;
  tags: Record<string, unknown> | null;
  temperature: number | null;
  frequency_penalty: number | null;
  presence_penalty: number | null;
  extra_body: Record<string, unknown> | null;
  default_params: Record<string, unknown> | null;
  prompt_template_id: string | null;
  has_api_key: boolean;
  created_at: string;
  updated_at: string;
};

type PromptTemplate = {
  id: string;
  name: string;
  version: number;
  template_text: string;
  input_schema: Record<string, unknown> | null;
  content_hash: string;
  created_at: string;
};

type AdminTaskSet = {
  id: string;
  name: string;
  description: string | null;
  metadata: Record<string, unknown> | null;
};

type AdminTask = {
  id: string;
  task_set_id: string | null;
  source_lang: string;
  target_lang: string;
  source_text: string;
  metadata: Record<string, unknown> | null;
};

async function mockAuthenticatedSession(page: Page, accessToken = "frontend-admin-access-token"): Promise<void> {
  await page.route("**/api/auth/session*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        user: { name: "Arena Admin", email: "admin@example.com" },
        expires: "2099-01-01T00:00:00.000Z",
        accessToken,
      }),
    });
  });
}

function modelRecord(overrides: Partial<AdminModel> = {}): AdminModel {
  return {
    id: "model-1",
    display_name: "Model One",
    provider_type: "openai_compat",
    model_name: "gpt-4o-mini",
    base_url: "http://127.0.0.1:18080",
    enabled: true,
    visibility: "public",
    tags: null,
    temperature: null,
    frequency_penalty: null,
    presence_penalty: null,
    extra_body: null,
    default_params: null,
    prompt_template_id: null,
    has_api_key: true,
    created_at: "2026-02-19T00:00:00.000Z",
    updated_at: "2026-02-19T00:00:00.000Z",
    ...overrides,
  };
}

test("admin models supports test, update, clear key, and delete", async ({ page }) => {
  const updateCalls: Array<{ authHeader: string | undefined; payload: Record<string, unknown> }> = [];
  const testCalls: Array<{ authHeader: string | undefined; id: string }> = [];
  const deleteCalls: Array<{ authHeader: string | undefined; id: string }> = [];

  let models: AdminModel[] = [modelRecord()];

  await mockAuthenticatedSession(page);

  await page.route("**/api/v1/admin/models", async (route) => {
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

  await page.route(/\/api\/v1\/admin\/models\/[^/]+$/, async (route) => {
    const { pathname } = new URL(route.request().url());
    const modelId = pathname.split("/").pop() ?? "";

    if (route.request().method() === "PUT") {
      const payload = route.request().postDataJSON() as Record<string, unknown>;
      updateCalls.push({
        authHeader: route.request().headers()["authorization"],
        payload,
      });

      models = models.map((model) => {
        if (model.id !== modelId) return model;

        let hasApiKey = model.has_api_key;
        if ("api_key" in payload) {
          if (payload.api_key === null) {
            hasApiKey = false;
          } else if (typeof payload.api_key === "string" && payload.api_key.length > 0) {
            hasApiKey = true;
          }
        }

        return {
          ...model,
          ...payload,
          has_api_key: hasApiKey,
        } as AdminModel;
      });

      const updated = models.find((model) => model.id === modelId);
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(updated),
      });
      return;
    }

    if (route.request().method() === "DELETE") {
      deleteCalls.push({
        authHeader: route.request().headers()["authorization"],
        id: modelId,
      });
      models = models.filter((model) => model.id !== modelId);

      await route.fulfill({ status: 204, body: "" });
      return;
    }

    await route.abort();
  });

  await page.route(/\/api\/v1\/admin\/models\/[^/]+\/test$/, async (route) => {
    const { pathname } = new URL(route.request().url());
    const parts = pathname.split("/");
    const modelId = parts[parts.length - 2] ?? "";

    testCalls.push({
      authHeader: route.request().headers()["authorization"],
      id: modelId,
    });

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ok: true, note: "ping ok", model_id: modelId, has_api_key: true }),
    });
  });

  await page.goto("/admin/models");

  const modelRow = page.locator("tr").filter({ hasText: "Model One" }).first();
  await expect(modelRow).toBeVisible();

  await modelRow.getByRole("button", { name: "Edit" }).click();
  const editSection = page.locator("section").filter({ hasText: "Edit model" });

  await editSection.getByRole("button", { name: "Test" }).click();
  await expect(editSection.getByText("Test: ok (ping ok)")).toBeVisible();

  await page.locator("#edit-display-name").fill("Model One Updated");
  await page.locator("#edit-api-key").fill("will-be-cleared");
  await page.getByLabel("clear api_key").check();

  await page.getByRole("button", { name: "Save" }).click();

  await expect(page.locator("tr").filter({ hasText: "Model One Updated" })).toHaveCount(1);

  expect(testCalls).toHaveLength(1);
  expect(testCalls[0]?.authHeader).toBe("Bearer frontend-admin-access-token");
  expect(testCalls[0]?.id).toBe("model-1");

  expect(updateCalls).toHaveLength(1);
  expect(updateCalls[0]?.authHeader).toBe("Bearer frontend-admin-access-token");
  expect(updateCalls[0]?.payload).toMatchObject({
    display_name: "Model One Updated",
    api_key: null,
  });

  const updatedRow = page.locator("tr").filter({ hasText: "Model One Updated" }).first();
  page.once("dialog", (dialog) => dialog.accept());
  await updatedRow.getByRole("button", { name: "Delete" }).click();

  await expect(page.locator("tr").filter({ hasText: "Model One Updated" })).toHaveCount(0);

  expect(deleteCalls).toHaveLength(1);
  expect(deleteCalls[0]?.authHeader).toBe("Bearer frontend-admin-access-token");
  expect(deleteCalls[0]?.id).toBe("model-1");
});

test("admin prompts increments version for repeated names", async ({ page }) => {
  const createCalls: Array<{ authHeader: string | undefined; payload: Record<string, unknown> }> = [];

  let templates: PromptTemplate[] = [];

  await mockAuthenticatedSession(page);

  await page.route("**/api/v1/admin/prompt-templates", async (route) => {
    const method = route.request().method();

    if (method === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ prompt_templates: templates }),
      });
      return;
    }

    if (method === "POST") {
      const payload = route.request().postDataJSON() as Record<string, unknown>;
      createCalls.push({
        authHeader: route.request().headers()["authorization"],
        payload,
      });

      const name = String(payload.name ?? "");
      const currentVersion = templates
        .filter((template) => template.name === name)
        .reduce((maxVersion, template) => Math.max(maxVersion, template.version), 0);

      const created: PromptTemplate = {
        id: `prompt-${templates.length + 1}`,
        name,
        version: currentVersion + 1,
        template_text: String(payload.template_text ?? ""),
        input_schema:
          payload.input_schema && typeof payload.input_schema === "object"
            ? (payload.input_schema as Record<string, unknown>)
            : null,
        content_hash: `content-hash-${templates.length + 1}`,
        created_at: "2026-02-19T00:00:00.000Z",
      };

      templates = [created, ...templates];

      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(created),
      });
      return;
    }

    await route.abort();
  });

  await page.goto("/admin/prompts");

  await expect(page.getByText("No prompt templates yet.")).toBeVisible();

  await page.locator("#prompt-name").fill("jp2zh_vn_translation");
  await page
    .locator("#prompt-template-text")
    .fill("You are a precise JP->ZH translation assistant. (v1)");
  await page.getByRole("button", { name: "Create" }).click();

  await expect(page.getByText("v1")).toBeVisible();

  await page
    .locator("#prompt-template-text")
    .fill("You are a precise JP->ZH translation assistant. (v2)");
  await page.getByRole("button", { name: "Create" }).click();

  const rows = page.locator("tbody tr");
  await expect(rows).toHaveCount(2);
  await expect(rows.first()).toContainText("jp2zh_vn_translation");
  await expect(rows.first()).toContainText("v2");

  expect(createCalls).toHaveLength(2);
  expect(createCalls[0]?.authHeader).toBe("Bearer frontend-admin-access-token");
  expect(createCalls[1]?.authHeader).toBe("Bearer frontend-admin-access-token");
  expect(createCalls[0]?.payload).toMatchObject({ name: "jp2zh_vn_translation" });
  expect(createCalls[1]?.payload).toMatchObject({ name: "jp2zh_vn_translation" });
});

test("admin tasks supports task-set/task CRUD and jsonl import", async ({ page }) => {
  const createTaskSetCalls: Array<{ authHeader: string | undefined; payload: Record<string, unknown> }> = [];
  const createTaskCalls: Array<{ authHeader: string | undefined; payload: Record<string, unknown> }> = [];
  const updateTaskSetCalls: Array<{ authHeader: string | undefined; payload: Record<string, unknown> }> = [];
  const updateTaskCalls: Array<{ authHeader: string | undefined; payload: Record<string, unknown> }> = [];
  const deleteTaskCalls: Array<{ authHeader: string | undefined; id: string }> = [];
  const importCalls: Array<{
    authHeader: string | undefined;
    taskSetId: string | null;
    sourceLang: string | null;
    targetLang: string | null;
  }> = [];

  let taskSetCounter = 1;
  let taskCounter = 1;
  let taskSets: AdminTaskSet[] = [];
  let tasks: AdminTask[] = [];

  await mockAuthenticatedSession(page);

  await page.route("**/api/v1/admin/task-sets", async (route) => {
    const method = route.request().method();

    if (method === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ task_sets: taskSets }),
      });
      return;
    }

    if (method === "POST") {
      const payload = route.request().postDataJSON() as Record<string, unknown>;
      createTaskSetCalls.push({
        authHeader: route.request().headers()["authorization"],
        payload,
      });

      const created: AdminTaskSet = {
        id: `set-${taskSetCounter}`,
        name: String(payload.name ?? ""),
        description: typeof payload.description === "string" ? payload.description : null,
        metadata:
          payload.metadata && typeof payload.metadata === "object"
            ? (payload.metadata as Record<string, unknown>)
            : null,
      };
      taskSetCounter += 1;
      taskSets = [created, ...taskSets];

      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(created),
      });
      return;
    }

    await route.abort();
  });

  await page.route(/\/api\/v1\/admin\/task-sets\/[^/]+$/, async (route) => {
    const { pathname } = new URL(route.request().url());
    const setId = pathname.split("/").pop() ?? "";

    if (route.request().method() === "PUT") {
      const payload = route.request().postDataJSON() as Record<string, unknown>;
      updateTaskSetCalls.push({
        authHeader: route.request().headers()["authorization"],
        payload,
      });

      taskSets = taskSets.map((taskSet) => {
        if (taskSet.id !== setId) return taskSet;
        return {
          ...taskSet,
          ...payload,
        } as AdminTaskSet;
      });

      const updated = taskSets.find((taskSet) => taskSet.id === setId);
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(updated),
      });
      return;
    }

    await route.abort();
  });

  await page.route("**/api/v1/admin/tasks/import-jsonl*", async (route) => {
    if (route.request().method() === "OPTIONS") {
      await route.fulfill({
        status: 204,
        headers: {
          "access-control-allow-origin": "*",
          "access-control-allow-headers": "*",
          "access-control-allow-methods": "POST, OPTIONS",
        },
      });
      return;
    }

    if (route.request().method() !== "POST") {
      await route.abort();
      return;
    }

    const requestUrl = new URL(route.request().url());
    const taskSetId = requestUrl.searchParams.get("task_set_id");
    const sourceLang = requestUrl.searchParams.get("source_lang");
    const targetLang = requestUrl.searchParams.get("target_lang");

    importCalls.push({
      authHeader: route.request().headers()["authorization"],
      taskSetId,
      sourceLang,
      targetLang,
    });

    tasks = [
      {
        id: `task-${taskCounter}`,
        task_set_id: taskSetId,
        source_lang: sourceLang ?? "ja",
        target_lang: targetLang ?? "zh",
        source_text: "Imported line from jsonl",
        metadata: null,
      },
      ...tasks,
    ];
    taskCounter += 1;

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        imported: 1,
        task_set_id: taskSetId,
        filename: "batch.jsonl",
      }),
    });
  });

  await page.route(/\/api\/v1\/admin\/tasks(?:\?.*)?$/, async (route) => {
    const method = route.request().method();
    const requestUrl = new URL(route.request().url());

    if (method === "GET") {
      const taskSetId = requestUrl.searchParams.get("task_set_id");
      const visibleTasks = taskSetId ? tasks.filter((task) => task.task_set_id === taskSetId) : tasks;

      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ tasks: visibleTasks }),
      });
      return;
    }

    if (method === "POST") {
      const payload = route.request().postDataJSON() as Record<string, unknown>;
      createTaskCalls.push({
        authHeader: route.request().headers()["authorization"],
        payload,
      });

      const created: AdminTask = {
        id: `task-${taskCounter}`,
        task_set_id: typeof payload.task_set_id === "string" ? payload.task_set_id : null,
        source_lang: String(payload.source_lang ?? "ja"),
        target_lang: String(payload.target_lang ?? "zh"),
        source_text: String(payload.source_text ?? ""),
        metadata:
          payload.metadata && typeof payload.metadata === "object"
            ? (payload.metadata as Record<string, unknown>)
            : null,
      };

      taskCounter += 1;
      tasks = [created, ...tasks];

      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(created),
      });
      return;
    }

    await route.abort();
  });

  await page.route(/\/api\/v1\/admin\/tasks\/[^/?]+$/, async (route) => {
    const { pathname } = new URL(route.request().url());
    const taskId = pathname.split("/").pop() ?? "";

    if (route.request().method() === "PUT") {
      const payload = route.request().postDataJSON() as Record<string, unknown>;
      updateTaskCalls.push({
        authHeader: route.request().headers()["authorization"],
        payload,
      });

      tasks = tasks.map((task) => {
        if (task.id !== taskId) return task;
        return {
          ...task,
          ...payload,
        } as AdminTask;
      });

      const updated = tasks.find((task) => task.id === taskId);
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(updated),
      });
      return;
    }

    if (route.request().method() === "DELETE") {
      deleteTaskCalls.push({
        authHeader: route.request().headers()["authorization"],
        id: taskId,
      });

      tasks = tasks.filter((task) => task.id !== taskId);
      await route.fulfill({ status: 204, body: "" });
      return;
    }

    await route.abort();
  });

  await page.goto("/admin/tasks");

  await expect(page.getByText("Showing 0 task(s)")).toBeVisible();

  const createSetSection = page.locator("section").filter({ hasText: "Create task set" });
  await createSetSection.getByLabel("name").fill("Playwright Set");
  await createSetSection.getByLabel("description").fill("Curated for e2e");
  await createSetSection
    .getByLabel("metadata (optional JSON object)")
    .fill('{"source":"playwright"}');
  await createSetSection.getByRole("button", { name: "Create" }).click();

  const createdSetRadio = page.getByRole("radio", { name: /Playwright Set/ });
  await expect(createdSetRadio).toBeVisible();
  await createdSetRadio.click();

  const createTaskSection = page.locator("section").filter({ hasText: "Create single task" });
  await createTaskSection.getByLabel("source_text").fill("Playwright task line");
  await createTaskSection.getByRole("button", { name: "Create" }).click();
  await expect(page.getByText("Playwright task line")).toBeVisible();

  await page.locator("#edit-set-name").fill("Playwright Set Updated");
  const taskSetEditSection = page.locator("section").filter({ hasText: "Edit selected task set" });
  await taskSetEditSection.getByRole("button", { name: "Save" }).click();

  await expect(page.getByRole("radio", { name: /Playwright Set Updated/ })).toBeVisible();

  const importSection = page.locator("section").filter({ hasText: "Import tasks (.jsonl)" });
  await importSection.locator('input[type="file"]').setInputFiles({
    name: "batch.jsonl",
    mimeType: "application/x-ndjson",
    buffer: Buffer.from('{"source_text":"Imported line from jsonl"}\n', "utf-8"),
  });
  await importSection.getByRole("button", { name: "Import" }).click();

  await expect(page.getByText("Imported 1 tasks from batch.jsonl")).toBeVisible();
  await expect(page.getByText("Imported line from jsonl")).toBeVisible();

  await page.getByRole("radio", { name: "All tasks" }).click();

  const taskRow = page.locator("tr").filter({ hasText: "Playwright task line" }).first();
  await taskRow.getByRole("button", { name: "Edit" }).click();
  await page.locator("#edit-task-source-text").fill("Playwright task line updated");

  const saveTaskEditButton = page
    .locator("div")
    .filter({ hasText: "Edit task" })
    .getByRole("button", { name: "Save" });
  await saveTaskEditButton.click();

  await expect(page.getByText("Playwright task line updated")).toBeVisible();

  const updatedTaskRow = page.locator("tr").filter({ hasText: "Playwright task line updated" }).first();
  page.once("dialog", (dialog) => dialog.accept());
  await updatedTaskRow.getByRole("button", { name: "Delete" }).click();

  await expect(page.locator("tr").filter({ hasText: "Playwright task line updated" })).toHaveCount(0);

  expect(createTaskSetCalls).toHaveLength(1);
  expect(createTaskSetCalls[0]?.authHeader).toBe("Bearer frontend-admin-access-token");
  expect(createTaskSetCalls[0]?.payload).toMatchObject({ name: "Playwright Set" });

  expect(createTaskCalls).toHaveLength(1);
  expect(createTaskCalls[0]?.authHeader).toBe("Bearer frontend-admin-access-token");
  expect(createTaskCalls[0]?.payload).toMatchObject({
    task_set_id: "set-1",
    source_text: "Playwright task line",
  });

  expect(updateTaskSetCalls).toHaveLength(1);
  expect(updateTaskSetCalls[0]?.authHeader).toBe("Bearer frontend-admin-access-token");
  expect(updateTaskSetCalls[0]?.payload).toMatchObject({ name: "Playwright Set Updated" });

  expect(importCalls).toHaveLength(1);
  expect(importCalls[0]?.authHeader).toBe("Bearer frontend-admin-access-token");
  expect(importCalls[0]?.taskSetId).toBe("set-1");
  expect(importCalls[0]?.sourceLang).toBe("ja");
  expect(importCalls[0]?.targetLang).toBe("zh");

  expect(updateTaskCalls).toHaveLength(1);
  expect(updateTaskCalls[0]?.authHeader).toBe("Bearer frontend-admin-access-token");
  expect(updateTaskCalls[0]?.payload).toMatchObject({ source_text: "Playwright task line updated" });

  expect(deleteTaskCalls).toHaveLength(1);
  expect(deleteTaskCalls[0]?.authHeader).toBe("Bearer frontend-admin-access-token");
});

test("admin models surfaces 403 for signed-in non-admin users", async ({ page }) => {
  await mockAuthenticatedSession(page, "frontend-non-admin-token");

  await page.route("**/api/v1/admin/models", async (route) => {
    await route.fulfill({
      status: 403,
      contentType: "application/json",
      body: JSON.stringify({ detail: "admin access required" }),
    });
  });

  await page.goto("/admin/models");

  await expect(page.getByText(/GET \/admin\/models failed: 403 - admin access required/)).toBeVisible();
  await expect(page.getByText("Admin login required")).toHaveCount(0);
});

test("admin prompts surfaces 403 for signed-in non-admin users", async ({ page }) => {
  await mockAuthenticatedSession(page, "frontend-non-admin-token");

  await page.route("**/api/v1/admin/prompt-templates", async (route) => {
    await route.fulfill({
      status: 403,
      contentType: "application/json",
      body: JSON.stringify({ detail: "admin access required" }),
    });
  });

  await page.goto("/admin/prompts");

  await expect(
    page.getByText(/GET \/admin\/prompt-templates failed: 403 - admin access required/),
  ).toBeVisible();
  await expect(page.getByText("Admin login required")).toHaveCount(0);
});

test("admin tasks surfaces 403 for signed-in non-admin users", async ({ page }) => {
  await mockAuthenticatedSession(page, "frontend-non-admin-token");

  await page.route("**/api/v1/admin/task-sets", async (route) => {
    await route.fulfill({
      status: 403,
      contentType: "application/json",
      body: JSON.stringify({ detail: "admin access required" }),
    });
  });

  await page.route(/\/api\/v1\/admin\/tasks(?:\?.*)?$/, async (route) => {
    await route.fulfill({
      status: 403,
      contentType: "application/json",
      body: JSON.stringify({ detail: "admin access required" }),
    });
  });

  await page.goto("/admin/tasks");

  await expect(page.getByText(/failed: 403 - admin access required/)).toBeVisible();
  await expect(page.getByText("Admin login required")).toHaveCount(0);
});
