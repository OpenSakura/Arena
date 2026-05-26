// @vitest-environment jsdom

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createTestI18n, TestI18nProvider } from "@/i18n/test-utils";
import AdminModelsRoute from "./AdminModelsRoute";

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

function modelRecord(overrides: Record<string, unknown> = {}) {
  return {
    id: "model-1",
    display_name: "Model One",
    model_name: "gpt-4o-mini",
    base_url: "https://gateway.example/v1",
    enabled: true,
    visibility: "public",
    tags: null,
    temperature: null,
    frequency_penalty: null,
    presence_penalty: null,
    system_prompt: null,
    user_prompt: null,
    params: null,
    has_api_key: true,
    created_at: "2026-02-18T00:00:00Z",
    updated_at: "2026-02-18T00:00:00Z",
    ...overrides,
  };
}

async function renderAdminModelsRoute(locale: "en" | "zh" = "en") {
  const i18n = await createTestI18n(locale);
  return render(
    <TestI18nProvider i18n={i18n}>
      <AdminModelsRoute />
    </TestI18nProvider>,
  );
}

describe("AdminModelsRoute", () => {
  it("does not load models when unauthenticated and shows empty state", async () => {
    await renderAdminModelsRoute();

    await screen.findByText("Model Registry");
    expect(apiGetMock).not.toHaveBeenCalled();

    await screen.findByText("No models yet.");
  });

  it("renders model registry labels from the Chinese catalog", async () => {
    await renderAdminModelsRoute("zh");

    await screen.findByRole("heading", { name: "模型注册表" });
    expect(screen.getByText("创建模型")).toBeDefined();
    expect(screen.getByLabelText("显示名称")).toBeDefined();
    expect(screen.getByLabelText("模型名称")).toBeDefined();
    expect(screen.getByLabelText("基础 URL")).toBeDefined();
    expect(screen.getByRole("button", { name: "创建" })).toBeDefined();
    expect(screen.getByText("暂无模型。" )).toBeDefined();
  });

  it("loads and renders model rows when authenticated", async () => {
    authenticatedSession();
    apiGetMock.mockResolvedValue({ models: [modelRecord()] });

    await renderAdminModelsRoute();

    await screen.findByText("Model One");

    expect(apiGetMock).toHaveBeenCalledWith("/admin/models?limit=1000");

    expect(screen.getAllByText("yes").length).toBeGreaterThanOrEqual(2);
  });

  it("creates a model and appends it to the table", async () => {
    authenticatedSession();
    apiGetMock.mockResolvedValue({ models: [] });
    apiPostMock.mockResolvedValue(
      modelRecord({
        id: "model-2",
        display_name: "Model Two",
        model_name: "gpt-two",
      }),
    );

    await renderAdminModelsRoute();
    await screen.findByText("No models yet.");

    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Display name"), "Model Two");
    await user.type(screen.getByLabelText("Model name"), "gpt-two");
    await user.type(screen.getByLabelText("Base URL"), "https://gateway-two.example/v1");
    await user.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(apiPostMock).toHaveBeenCalledWith(
        "/admin/models",
        {
          display_name: "Model Two",
          model_name: "gpt-two",
          base_url: "https://gateway-two.example/v1",
          enabled: true,
          visibility: "public",
          system_prompt: null,
          user_prompt: null,
        },
      );
    });
    expect(apiPostMock.mock.calls[0]).toHaveLength(2);

    await screen.findByText("Model Two");
  });

  it("preserves the model create payload keys and values under Chinese locale", async () => {
    authenticatedSession();
    apiGetMock.mockResolvedValue({ models: [] });
    apiPostMock.mockResolvedValue(
      modelRecord({
        id: "model-localized",
        display_name: "Localized Payload Model",
        model_name: "gpt-localized",
        base_url: "https://gateway-localized.example/v1",
        visibility: "private",
        tags: { family: "openai", tier: "cheap" },
        params: { route: "jp2zh", top_p: 0.95, max_tokens: 1024 },
      }),
    );

    await renderAdminModelsRoute("zh");
    await screen.findByText("暂无模型。");

    const user = userEvent.setup();
    await user.type(screen.getByLabelText("显示名称"), "Localized Payload Model");
    await user.type(screen.getByLabelText("模型名称"), "gpt-localized");
    await user.type(screen.getByLabelText("基础 URL"), "https://gateway-localized.example/v1");
    await user.selectOptions(screen.getByLabelText("可见性"), "private");

    const tagsTextarea = document.getElementById("create-tags");
    const paramsTextarea = document.getElementById("create-params");
    if (!(tagsTextarea instanceof HTMLTextAreaElement) || !(paramsTextarea instanceof HTMLTextAreaElement)) {
      throw new Error("JSON payload textareas not found");
    }
    fireEvent.change(tagsTextarea, { target: { value: '{"family":"openai","tier":"cheap"}' } });
    fireEvent.change(paramsTextarea, { target: { value: '{"route":"jp2zh","top_p":0.95,"max_tokens":1024}' } });

    await user.click(screen.getByRole("button", { name: "创建" }));

    await waitFor(() => {
      expect(apiPostMock).toHaveBeenCalledWith(
        "/admin/models",
        {
          display_name: "Localized Payload Model",
          model_name: "gpt-localized",
          base_url: "https://gateway-localized.example/v1",
          enabled: true,
          visibility: "private",
          tags: { family: "openai", tier: "cheap" },
          params: { route: "jp2zh", top_p: 0.95, max_tokens: 1024 },
          system_prompt: null,
          user_prompt: null,
        },
      );
    });
    expect(apiPostMock.mock.calls[0]).toHaveLength(2);
  });

  it("shows validation errors before create API call", async () => {
    authenticatedSession();
    apiGetMock.mockResolvedValue({ models: [] });

    await renderAdminModelsRoute();
    await screen.findByText("No models yet.");

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Create" }));

    await screen.findByText("display_name is required");
    expect(apiPostMock).not.toHaveBeenCalled();
  });

  it("saves model edits through the update endpoint", async () => {
    authenticatedSession();
    apiGetMock.mockResolvedValue({ models: [modelRecord()] });
    apiPutMock.mockResolvedValue(
      modelRecord({
        display_name: "Model One Server Normalized",
        frequency_penalty: 0.5,
      }),
    );

    await renderAdminModelsRoute();
    await screen.findByText("Model One");

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Edit" }));

    const displayNameInput = document.getElementById("edit-display-name");
    if (!(displayNameInput instanceof HTMLInputElement)) {
      throw new Error("Edit display name input not found");
    }
    await user.clear(displayNameInput);
    await user.type(displayNameInput, "Model One Renamed");

    const frequencyPenaltyInput = document.getElementById("edit-fp");
    if (!(frequencyPenaltyInput instanceof HTMLInputElement)) {
      throw new Error("Edit frequency penalty input not found");
    }
    await user.type(frequencyPenaltyInput, "0.25");

    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(apiPutMock).toHaveBeenCalledWith(
        "/admin/models/model-1",
        expect.objectContaining({
          display_name: "Model One Renamed",
          model_name: "gpt-4o-mini",
          base_url: "https://gateway.example/v1",
          enabled: true,
          visibility: "public",
          temperature: null,
          frequency_penalty: 0.25,
          presence_penalty: null,
          system_prompt: null,
          user_prompt: null,
          tags: null,
          params: null,
        }),
      );
    });
    expect(apiPutMock.mock.calls[0]).toHaveLength(2);

    await screen.findByText("Model One Server Normalized");
    expect(displayNameInput.value).toBe("Model One Server Normalized");
    expect((frequencyPenaltyInput as HTMLInputElement).value).toBe("0.5");
  });

  it("deletes a model when confirmed", async () => {
    authenticatedSession();
    apiGetMock.mockResolvedValue({ models: [modelRecord()] });
    apiDeleteMock.mockResolvedValue(null);
    vi.spyOn(window, "confirm").mockReturnValue(true);

    await renderAdminModelsRoute();
    await screen.findByText("Model One");

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() => {
      expect(apiDeleteMock).toHaveBeenCalledWith("/admin/models/model-1");
    });
    expect(apiDeleteMock.mock.calls[0]).toHaveLength(1);

    await waitFor(() => {
      expect(screen.queryByText("Model One")).toBeNull();
    });
  });

  it("enforces mutual exclusivity between clear api_key and new api_key inputs", async () => {
    authenticatedSession();
    apiGetMock.mockResolvedValue({ models: [modelRecord()] });

    await renderAdminModelsRoute();
    await screen.findByText("Model One");

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Edit" }));

    const apiKeyInput = screen.getByLabelText("New API key (optional)") as HTMLInputElement;
    const clearApiKeyCheckbox = screen.getByLabelText("Clear API key") as HTMLInputElement;

    await user.click(clearApiKeyCheckbox);
    expect(clearApiKeyCheckbox.checked).toBe(true);
    expect(apiKeyInput.disabled).toBe(true);

    await user.click(clearApiKeyCheckbox);
    expect(clearApiKeyCheckbox.checked).toBe(false);
    expect(apiKeyInput.disabled).toBe(false);
    
    await user.type(apiKeyInput, "new-secret-key");
    expect(apiKeyInput.value).toBe("new-secret-key");

    await user.click(clearApiKeyCheckbox);
    expect(clearApiKeyCheckbox.checked).toBe(true);
    expect(apiKeyInput.value).toBe("");
    expect(apiKeyInput.disabled).toBe(true);
  });

  it("calls the model test endpoint from table action", async () => {
    authenticatedSession();
    apiGetMock.mockResolvedValue({ models: [modelRecord()] });
    apiPostMock.mockResolvedValue({ ok: true, model_id: "model-1", has_api_key: true });

    await renderAdminModelsRoute();
    await screen.findByText("Model One");

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Test" }));

    await waitFor(() => {
      expect(apiPostMock).toHaveBeenCalledWith(
        "/admin/models/model-1/test",
        {},
      );
    });
  });

  it("includes non-blank system_prompt and user_prompt in the create payload", async () => {
    authenticatedSession();
    apiGetMock.mockResolvedValue({ models: [] });
    apiPostMock.mockResolvedValue(
      modelRecord({
        id: "model-3",
        display_name: "Prompt Model",
        model_name: "gpt-prompt",
        system_prompt: "You are an expert translator.",
        user_prompt: "Translate from {{ source_lang }} to {{ target_lang }}:\n{{ source_text }}",
      }),
    );

    await renderAdminModelsRoute();
    await screen.findByText("No models yet.");

    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Display name"), "Prompt Model");
    await user.type(screen.getByLabelText("Model name"), "gpt-prompt");
    await user.type(screen.getByLabelText("Base URL"), "https://gateway.example/v1");

    const systemPromptTextarea = document.getElementById("create-system-prompt");
    if (!(systemPromptTextarea instanceof HTMLTextAreaElement)) {
      throw new Error("create-system-prompt textarea not found");
    }
    fireEvent.change(systemPromptTextarea, {
      target: { value: "You are an expert translator." },
    });

    const userPromptTextarea = document.getElementById("create-user-prompt");
    if (!(userPromptTextarea instanceof HTMLTextAreaElement)) {
      throw new Error("create-user-prompt textarea not found");
    }
    fireEvent.change(userPromptTextarea, {
      target: {
        value: "Translate from {{ source_lang }} to {{ target_lang }}:\n{{ source_text }}",
      },
    });

    await user.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(apiPostMock).toHaveBeenCalledWith(
        "/admin/models",
        expect.objectContaining({
          system_prompt: "You are an expert translator.",
          user_prompt:
            "Translate from {{ source_lang }} to {{ target_lang }}:\n{{ source_text }}",
        }),
      );
    });
  });

  it("includes non-blank system_prompt and user_prompt in the edit payload", async () => {
    authenticatedSession();
    apiGetMock.mockResolvedValue({
      models: [
        modelRecord({
          system_prompt: "You are an expert translator.",
          user_prompt: "Translate from {{ source_lang }} to {{ target_lang }}:\n{{ source_text }}",
        }),
      ],
    });
    apiPutMock.mockResolvedValue(
      modelRecord({
        system_prompt: "You are an expert translator.",
        user_prompt: "Translate from {{ source_lang }} to {{ target_lang }}:\n{{ source_text }}",
      }),
    );

    await renderAdminModelsRoute();
    await screen.findByText("Model One");

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Edit" }));

    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(apiPutMock).toHaveBeenCalledWith(
        "/admin/models/model-1",
        expect.objectContaining({
          system_prompt: "You are an expert translator.",
          user_prompt:
            "Translate from {{ source_lang }} to {{ target_lang }}:\n{{ source_text }}",
        }),
      );
    });
  });
});
