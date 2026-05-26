// @vitest-environment jsdom

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createTestI18n, TestI18nProvider } from "@/i18n/test-utils";
import AdminTasksRoute from "./AdminTasksRoute";

const useAuthHeadersMock = vi.fn();
const apiGetMock = vi.fn();
const apiPostMock = vi.fn();
const apiPutMock = vi.fn();
const apiDeleteMock = vi.fn();

vi.mock("@/hooks/useAuthHeaders", () => ({
  useAuthHeaders: () => useAuthHeadersMock(),
}));

vi.mock("@/lib/api", () => ({
  apiGet: (...args: unknown[]) => apiGetMock(...args),
  apiPost: (...args: unknown[]) => apiPostMock(...args),
  apiPut: (...args: unknown[]) => apiPutMock(...args),
  apiDelete: (...args: unknown[]) => apiDeleteMock(...args),
}));

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

beforeEach(() => {
  useAuthHeadersMock.mockReset();
  apiGetMock.mockReset();
  apiPostMock.mockReset();
  apiPutMock.mockReset();
  apiDeleteMock.mockReset();

  useAuthHeadersMock.mockReturnValue({
    authStatus: "unauthenticated",
    sessionError: null,
    csrfToken: null,
  });
});

function authenticatedSession() {
  useAuthHeadersMock.mockReturnValue({
    authStatus: "authenticated",
    csrfToken: "csrf-token",
    sessionError: null,
  });
}

function taskSetRecord(overrides: Record<string, unknown> = {}) {
  return {
    id: "set-1",
    name: "Public Samples",
    description: "curated",
    metadata: null,
    ...overrides,
  };
}

function taskRecord(overrides: Record<string, unknown> = {}) {
  return {
    id: "task-1",
    task_set_id: "set-1",
    source_lang: "ja",
    target_lang: "zh",
    source_text: "Sample source",
    metadata: null,
    ...overrides,
  };
}

async function renderAdminTasksRoute(locale: "en" | "zh" = "en") {
  const i18n = await createTestI18n(locale);

  return render(
    <TestI18nProvider i18n={i18n}>
      <AdminTasksRoute />
    </TestI18nProvider>,
  );
}

describe("AdminTasksRoute", () => {
  it("does not make API calls when user is not authenticated and shows empty state", async () => {
    await renderAdminTasksRoute();

    await screen.findByRole("heading", { name: "Tasks & Task Sets" });
    expect(apiGetMock).not.toHaveBeenCalled();

    await screen.findByText("Showing 0 task(s)");
  });

  it("renders task route labels from the Chinese catalog", async () => {
    await renderAdminTasksRoute("zh");

    await screen.findByRole("heading", { name: "任务与任务集" });
    expect(screen.getByText("创建任务集")).toBeDefined();
    expect(screen.getByLabelText("名称")).toBeDefined();
    expect(screen.getByLabelText("描述")).toBeDefined();
    expect(screen.getAllByLabelText("元数据（JSON 对象，可选）")).toHaveLength(2);
    expect(screen.getByText("创建单个任务")).toBeDefined();
    expect(screen.getByLabelText("源语言代码")).toBeDefined();
    expect(screen.getByLabelText("目标语言代码")).toBeDefined();
    expect(screen.getByLabelText("原文")).toBeDefined();
    expect(screen.getByText("导入任务（.jsonl）")).toBeDefined();
    expect(screen.getByLabelText("选择要导入的 JSONL 文件")).toBeDefined();
    expect(screen.getByLabelText("默认源语言代码")).toBeDefined();
    expect(screen.getByLabelText("默认目标语言代码")).toBeDefined();
    expect(screen.getByText("共显示 0 个任务")).toBeDefined();
  });

  it("loads task sets and tasks when authenticated", async () => {
    authenticatedSession();

    apiGetMock.mockImplementation((path: string) => {
      if (path.startsWith("/admin/task-sets")) {
        return Promise.resolve({ task_sets: [taskSetRecord()] });
      }

      if (path.startsWith("/admin/tasks")) {
        return Promise.resolve({ tasks: [taskRecord({ source_text: "テストです" })] });
      }

      throw new Error(`unexpected path: ${path}`);
    });

    await renderAdminTasksRoute();

    await screen.findByText("Public Samples");
    await screen.findByText("テストです");

    expect(apiGetMock).toHaveBeenCalledWith("/admin/task-sets?limit=1000");
    expect(apiGetMock).toHaveBeenCalledWith("/admin/tasks?limit=1000");

    await waitFor(() => {
      expect(screen.getByText("Showing 1 task(s)")).toBeDefined();
    });
  });

  it("creates a task set and then creates a task under that set", async () => {
    authenticatedSession();

    apiGetMock.mockImplementation((path: string) => {
      if (path.startsWith("/admin/task-sets")) {
        return Promise.resolve({ task_sets: [] });
      }
      if (path.startsWith("/admin/tasks")) {
        return Promise.resolve({ tasks: [] });
      }
      throw new Error(`unexpected path: ${path}`);
    });

    apiPostMock.mockImplementation((path: string) => {
      if (path.startsWith("/admin/task-sets")) {
        return Promise.resolve(
          taskSetRecord({
            id: "set-2",
            name: "Set Two",
            description: "manually curated",
            metadata: { source: "manual" },
          }),
        );
      }
      if (path.startsWith("/admin/tasks")) {
        return Promise.resolve(
          taskRecord({
            id: "task-2",
            task_set_id: "set-2",
            source_text: "JP line",
          }),
        );
      }
      throw new Error(`unexpected post path: ${path}`);
    });

    await renderAdminTasksRoute();
    await screen.findByText("Showing 0 task(s)");

    const user = userEvent.setup();

    const createSetSection = screen.getByText("Create task set").closest("section");
    if (!createSetSection) throw new Error("Create task set section not found");

    await user.type(within(createSetSection).getByLabelText("Name"), "Set Two");
    await user.type(within(createSetSection).getByLabelText("Description"), "manually curated");
    fireEvent.change(within(createSetSection).getByLabelText("Metadata (optional JSON object)"), {
      target: { value: '{"source":"manual"}' },
    });
    await user.click(within(createSetSection).getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(apiPostMock).toHaveBeenCalledWith(
        "/admin/task-sets",
        {
          name: "Set Two",
          description: "manually curated",
          metadata: { source: "manual" },
        },
      );
    });
    expect(apiPostMock.mock.calls[0]).toHaveLength(2);

    await screen.findByRole("radio", { name: /Set Two/ });
    await user.click(screen.getByRole("radio", { name: /Set Two/ }));

    const createTaskSection = screen.getByText("Create single task").closest("section");
    if (!createTaskSection) throw new Error("Create single task section not found");

    await user.type(within(createTaskSection).getByLabelText("Source text"), "JP line");
    await user.click(within(createTaskSection).getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(apiPostMock).toHaveBeenNthCalledWith(
        2,
        "/admin/tasks",
        {
          task_set_id: "set-2",
          source_lang: "ja",
          target_lang: "zh",
          source_text: "JP line",
          metadata: null,
        },
      );
    });

    await screen.findByText("JP line");
  });

  it("keeps default source_lang and target_lang task payload values under Chinese locale", async () => {
    authenticatedSession();

    apiGetMock.mockImplementation((path: string) => {
      if (path.startsWith("/admin/task-sets")) {
        return Promise.resolve({ task_sets: [] });
      }
      if (path.startsWith("/admin/tasks")) {
        return Promise.resolve({ tasks: [] });
      }
      throw new Error(`unexpected path: ${path}`);
    });

    apiPostMock.mockResolvedValue(
      taskRecord({
        id: "task-localized",
        task_set_id: null,
        source_text: "JP line",
      }),
    );

    await renderAdminTasksRoute("zh");
    await screen.findByText("共显示 0 个任务");

    const user = userEvent.setup();
    const createTaskSection = screen.getByText("创建单个任务").closest("section");
    if (!createTaskSection) throw new Error("Create single task section not found");

    const sourceLangInput = within(createTaskSection).getByLabelText("源语言代码") as HTMLInputElement;
    const targetLangInput = within(createTaskSection).getByLabelText("目标语言代码") as HTMLInputElement;
    expect(sourceLangInput.value).toBe("ja");
    expect(targetLangInput.value).toBe("zh");

    await user.type(within(createTaskSection).getByLabelText("原文"), "JP line");
    await user.click(within(createTaskSection).getByRole("button", { name: "创建" }));

    await waitFor(() => {
      expect(apiPostMock).toHaveBeenCalledWith(
        "/admin/tasks",
        {
          task_set_id: null,
          source_lang: "ja",
          target_lang: "zh",
          source_text: "JP line",
          metadata: null,
        },
      );
    });
  });

  it("updates and deletes the selected task set", async () => {
    authenticatedSession();

    apiGetMock.mockImplementation((path: string) => {
      if (path.startsWith("/admin/task-sets")) {
        return Promise.resolve({ task_sets: [taskSetRecord()] });
      }
      if (path.startsWith("/admin/tasks")) {
        return Promise.resolve({ tasks: [] });
      }
      throw new Error(`unexpected path: ${path}`);
    });

    apiPutMock.mockResolvedValue(taskSetRecord({ name: "Renamed Samples" }));
    apiDeleteMock.mockResolvedValue(null);
    vi.spyOn(window, "confirm").mockReturnValue(true);

    await renderAdminTasksRoute();
    await screen.findByRole("radio", { name: /Public Samples/ });

    const user = userEvent.setup();
    await user.click(screen.getByRole("radio", { name: /Public Samples/ }));

    const nameInput = document.getElementById("edit-set-name");
    if (!(nameInput instanceof HTMLInputElement)) {
      throw new Error("Edit task set name input not found");
    }
    await user.clear(nameInput);
    await user.type(nameInput, "Renamed Samples");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(apiPutMock).toHaveBeenCalledWith(
        "/admin/task-sets/set-1",
        {
          name: "Renamed Samples",
          description: "curated",
          metadata: null,
        },
      );
    });
    expect(apiPutMock.mock.calls[0]).toHaveLength(2);

    await screen.findByRole("radio", { name: /Renamed Samples/ });

    await user.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() => {
      expect(apiDeleteMock).toHaveBeenCalledWith("/admin/task-sets/set-1");
    });
    expect(apiDeleteMock.mock.calls[0]).toHaveLength(1);

    await waitFor(() => {
      expect(screen.queryByRole("radio", { name: /Renamed Samples/ })).toBeNull();
    });
  });

  it("edits and deletes a task row", async () => {
    authenticatedSession();

    apiGetMock.mockImplementation((path: string) => {
      if (path.startsWith("/admin/task-sets")) {
        return Promise.resolve({ task_sets: [taskSetRecord()] });
      }
      if (path.startsWith("/admin/tasks")) {
        return Promise.resolve({ tasks: [taskRecord()] });
      }
      throw new Error(`unexpected path: ${path}`);
    });

    apiPutMock.mockResolvedValue(taskRecord({ source_text: "Updated source text" }));
    apiDeleteMock.mockResolvedValue(null);
    vi.spyOn(window, "confirm").mockReturnValue(true);

    await renderAdminTasksRoute();
    await screen.findByText("Sample source");

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Edit" }));

    const sourceTextInput = document.getElementById("edit-task-source-text");
    if (!(sourceTextInput instanceof HTMLTextAreaElement)) {
      throw new Error("Edit task source text input not found");
    }
    await user.clear(sourceTextInput);
    await user.type(sourceTextInput, "Updated source text");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(apiPutMock).toHaveBeenCalledWith(
        "/admin/tasks/task-1",
        {
          task_set_id: "set-1",
          source_lang: "ja",
          target_lang: "zh",
          source_text: "Updated source text",
          metadata: null,
        },
      );
    });

    await screen.findByText("Updated source text");

    await user.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() => {
      expect(apiDeleteMock).toHaveBeenCalledWith("/admin/tasks/task-1");
    });

    await waitFor(() => {
      expect(screen.queryByText("Updated source text")).toBeNull();
    });
  });

  it("imports jsonl tasks and refreshes the list", async () => {
    authenticatedSession();

    let refreshedTasks = { tasks: [] as Array<Record<string, unknown>> };

    apiGetMock.mockImplementation((path: string) => {
      if (path.startsWith("/admin/task-sets")) {
        return Promise.resolve({ task_sets: [taskSetRecord()] });
      }
      if (path.startsWith("/admin/tasks")) {
        return Promise.resolve(refreshedTasks);
      }
      throw new Error(`unexpected path: ${path}`);
    });

    apiPostMock.mockImplementation((path: string, body: unknown) => {
      if (path === "/admin/tasks/import-jsonl?source_lang=ja&target_lang=zh") {
        expect(body).toBeInstanceOf(FormData);
        refreshedTasks = {
          tasks: [
            taskRecord({
              id: "task-imported",
              source_text: "Imported source",
            }),
          ],
        };

        return Promise.resolve({
          ok: true,
          imported: 1,
          task_set_id: null,
          filename: "batch.jsonl",
        });
      }

      throw new Error(`unexpected post path: ${path}`);
    });

    await renderAdminTasksRoute("zh");
    await screen.findByText("共显示 0 个任务");

    const user = userEvent.setup();

    const fileInput = document.querySelector('input[type="file"]');
    if (!(fileInput instanceof HTMLInputElement)) {
      throw new Error("Import file input not found");
    }

    const file = new File(['{"source_text":"line"}\n'], "batch.jsonl", {
      type: "application/x-ndjson",
    });
    await user.upload(fileInput, file);

    const importSection = screen.getByText("导入任务（.jsonl）").closest("section");
    if (!importSection) throw new Error("Import section not found");
    expect((within(importSection).getByLabelText("默认源语言代码") as HTMLInputElement).value).toBe("ja");
    expect((within(importSection).getByLabelText("默认目标语言代码") as HTMLInputElement).value).toBe("zh");
    await user.click(within(importSection).getByRole("button", { name: "导入" }));

    await waitFor(() => {
      expect(apiPostMock).toHaveBeenCalledWith(
        "/admin/tasks/import-jsonl?source_lang=ja&target_lang=zh",
        expect.any(FormData),
      );
    });

    await screen.findByText("已从 batch.jsonl 导入 1 个任务");
    await screen.findByText("Imported source");
    expect(apiGetMock.mock.calls.filter(([path]) => path.startsWith("/admin/tasks")).length).toBeGreaterThan(1);
    expect(apiGetMock.mock.calls).toContainEqual([
      "/admin/tasks?limit=1000",
    ]);
  });

  it("shows an error when import is requested without a file", async () => {
    authenticatedSession();

    apiGetMock.mockImplementation((path: string) => {
      if (path.startsWith("/admin/task-sets")) {
        return Promise.resolve({ task_sets: [] });
      }
      if (path.startsWith("/admin/tasks")) {
        return Promise.resolve({ tasks: [] });
      }
      throw new Error(`unexpected path: ${path}`);
    });

    await renderAdminTasksRoute("zh");
    await screen.findByText("共显示 0 个任务");

    const user = userEvent.setup();
    const importSection = screen.getByText("导入任务（.jsonl）").closest("section");
    if (!importSection) throw new Error("Import section not found");
    await user.click(within(importSection).getByRole("button", { name: "导入" }));

    await screen.findByText("请先选择 .jsonl 文件");
    expect(apiPostMock).not.toHaveBeenCalled();
  });
});
