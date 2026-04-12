// @vitest-environment jsdom

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AdminTasksPage from "./page";

const useSessionMock = vi.fn();
const signInMock = vi.fn();
const apiGetMock = vi.fn();
const apiPostMock = vi.fn();
const apiPutMock = vi.fn();
const apiDeleteMock = vi.fn();

vi.mock("next-auth/react", () => ({
  signIn: (...args: unknown[]) => signInMock(...args),
  useSession: () => useSessionMock(),
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
  useSessionMock.mockReset();
  signInMock.mockReset();
  apiGetMock.mockReset();
  apiPostMock.mockReset();
  apiPutMock.mockReset();
  apiDeleteMock.mockReset();
});

function authenticatedSession() {
  useSessionMock.mockReturnValue({
    data: { accessToken: "admin-token" },
    status: "authenticated",
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

describe("AdminTasksPage", () => {
  it("does not make API calls when user is not authenticated and shows empty state", async () => {
    useSessionMock.mockReturnValue({ data: null, status: "unauthenticated" });

    render(<AdminTasksPage />);

    // Page still renders its UI (middleware handles redirect),
    // but no admin API calls are made without a token.
    await screen.findByText("Tasks & Task Sets");
    expect(apiGetMock).not.toHaveBeenCalled();

    await screen.findByText("Showing 0 task(s)");
  });

  it("loads task sets and tasks when authenticated", async () => {
    authenticatedSession();

    apiGetMock.mockImplementation((path: string) => {
      if (path === "/admin/task-sets") {
        return Promise.resolve({ task_sets: [taskSetRecord()] });
      }

      if (path === "/admin/tasks") {
        return Promise.resolve({ tasks: [taskRecord({ source_text: "テストです" })] });
      }

      throw new Error(`unexpected path: ${path}`);
    });

    render(<AdminTasksPage />);

    await screen.findByText("Public Samples");
    await screen.findByText("テストです");

    expect(apiGetMock).toHaveBeenCalledWith("/admin/task-sets", {
      headers: { Authorization: "Bearer admin-token" },
    });
    expect(apiGetMock).toHaveBeenCalledWith("/admin/tasks", {
      headers: { Authorization: "Bearer admin-token" },
    });

    await waitFor(() => {
      expect(screen.getByText("Showing 1 task(s)")).toBeDefined();
    });
  });

  it("creates a task set and then creates a task under that set", async () => {
    authenticatedSession();

    apiGetMock.mockImplementation((path: string) => {
      if (path === "/admin/task-sets") {
        return Promise.resolve({ task_sets: [] });
      }
      if (path === "/admin/tasks") {
        return Promise.resolve({ tasks: [] });
      }
      throw new Error(`unexpected path: ${path}`);
    });

    apiPostMock.mockImplementation((path: string) => {
      if (path === "/admin/task-sets") {
        return Promise.resolve(
          taskSetRecord({
            id: "set-2",
            name: "Set Two",
            description: "manually curated",
            metadata: { source: "manual" },
          }),
        );
      }
      if (path === "/admin/tasks") {
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

    render(<AdminTasksPage />);
    await screen.findByText("Showing 0 task(s)");

    const user = userEvent.setup();

    const createSetSection = screen.getByText("Create task set").closest("section");
    if (!createSetSection) throw new Error("Create task set section not found");

    await user.type(within(createSetSection).getByLabelText("name"), "Set Two");
    await user.type(within(createSetSection).getByLabelText("description"), "manually curated");
    fireEvent.change(within(createSetSection).getByLabelText("metadata (optional JSON object)"), {
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
        {
          headers: { Authorization: "Bearer admin-token" },
        },
      );
    });

    await screen.findByRole("radio", { name: /Set Two/ });
    await user.click(screen.getByRole("radio", { name: /Set Two/ }));

    const createTaskSection = screen.getByText("Create single task").closest("section");
    if (!createTaskSection) throw new Error("Create single task section not found");

    await user.type(within(createTaskSection).getByLabelText("source_text"), "JP line");
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
        {
          headers: { Authorization: "Bearer admin-token" },
        },
      );
    });

    await screen.findByText("JP line");
  });

  it("updates and deletes the selected task set", async () => {
    authenticatedSession();

    apiGetMock.mockImplementation((path: string) => {
      if (path === "/admin/task-sets") {
        return Promise.resolve({ task_sets: [taskSetRecord()] });
      }
      if (path === "/admin/tasks") {
        return Promise.resolve({ tasks: [] });
      }
      throw new Error(`unexpected path: ${path}`);
    });

    apiPutMock.mockResolvedValue(taskSetRecord({ name: "Renamed Samples" }));
    apiDeleteMock.mockResolvedValue(null);
    vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<AdminTasksPage />);
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
        {
          headers: { Authorization: "Bearer admin-token" },
        },
      );
    });

    await screen.findByRole("radio", { name: /Renamed Samples/ });

    await user.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() => {
      expect(apiDeleteMock).toHaveBeenCalledWith(
        "/admin/task-sets/set-1",
        {
          headers: { Authorization: "Bearer admin-token" },
        },
      );
    });

    await waitFor(() => {
      expect(screen.queryByRole("radio", { name: /Renamed Samples/ })).toBeNull();
    });
  });

  it("edits and deletes a task row", async () => {
    authenticatedSession();

    apiGetMock.mockImplementation((path: string) => {
      if (path === "/admin/task-sets") {
        return Promise.resolve({ task_sets: [taskSetRecord()] });
      }
      if (path === "/admin/tasks") {
        return Promise.resolve({ tasks: [taskRecord()] });
      }
      throw new Error(`unexpected path: ${path}`);
    });

    apiPutMock.mockResolvedValue(taskRecord({ source_text: "Updated source text" }));
    apiDeleteMock.mockResolvedValue(null);
    vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<AdminTasksPage />);
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
        {
          headers: { Authorization: "Bearer admin-token" },
        },
      );
    });

    await screen.findByText("Updated source text");

    await user.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() => {
      expect(apiDeleteMock).toHaveBeenCalledWith(
        "/admin/tasks/task-1",
        {
          headers: { Authorization: "Bearer admin-token" },
        },
      );
    });

    await waitFor(() => {
      expect(screen.queryByText("Updated source text")).toBeNull();
    });
  });

  it("imports jsonl tasks and refreshes the list", async () => {
    authenticatedSession();

    let refreshedTasks = { tasks: [] as Array<Record<string, unknown>> };

    apiGetMock.mockImplementation((path: string) => {
      if (path === "/admin/task-sets") {
        return Promise.resolve({ task_sets: [taskSetRecord()] });
      }
      if (path === "/admin/tasks") {
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

    render(<AdminTasksPage />);
    await screen.findByText("Showing 0 task(s)");

    const user = userEvent.setup();

    const fileInput = document.querySelector('input[type="file"]');
    if (!(fileInput instanceof HTMLInputElement)) {
      throw new Error("Import file input not found");
    }

    const file = new File(['{"source_text":"line"}\n'], "batch.jsonl", {
      type: "application/x-ndjson",
    });
    await user.upload(fileInput, file);

    const importSection = screen.getByText("Import tasks (.jsonl)").closest("section");
    if (!importSection) throw new Error("Import section not found");
    await user.click(within(importSection).getByRole("button", { name: "Import" }));

    await waitFor(() => {
      expect(apiPostMock).toHaveBeenCalledWith(
        "/admin/tasks/import-jsonl?source_lang=ja&target_lang=zh",
        expect.any(FormData),
        {
          headers: { Authorization: "Bearer admin-token" },
        },
      );
    });

    await screen.findByText("Imported 1 tasks from batch.jsonl");
    await screen.findByText("Imported source");
    expect(apiGetMock.mock.calls.filter(([path]) => path === "/admin/tasks").length).toBeGreaterThan(1);
  });

  it("shows an error when import is requested without a file", async () => {
    authenticatedSession();

    apiGetMock.mockImplementation((path: string) => {
      if (path === "/admin/task-sets") {
        return Promise.resolve({ task_sets: [] });
      }
      if (path === "/admin/tasks") {
        return Promise.resolve({ tasks: [] });
      }
      throw new Error(`unexpected path: ${path}`);
    });

    render(<AdminTasksPage />);
    await screen.findByText("Showing 0 task(s)");

    const user = userEvent.setup();
    const importSection = screen.getByText("Import tasks (.jsonl)").closest("section");
    if (!importSection) throw new Error("Import section not found");
    await user.click(within(importSection).getByRole("button", { name: "Import" }));

    await screen.findByText("Select a .jsonl file first");
    expect(apiPostMock).not.toHaveBeenCalled();
  });
});
