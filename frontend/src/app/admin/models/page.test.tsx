// @vitest-environment jsdom

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AdminModelsPage from "./page";

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

function modelRecord(overrides: Record<string, unknown> = {}) {
  return {
    id: "model-1",
    display_name: "Model One",
    provider_type: "openai_compat",
    model_name: "gpt-4o-mini",
    base_url: "https://gateway.example/v1",
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
    created_at: "2026-02-18T00:00:00Z",
    updated_at: "2026-02-18T00:00:00Z",
    ...overrides,
  };
}

describe("AdminModelsPage", () => {
  it("does not load models when unauthenticated and shows empty state", async () => {
    useSessionMock.mockReturnValue({ data: null, status: "unauthenticated" });

    render(<AdminModelsPage />);

    // Without a valid session, the page renders the heading but no API calls
    // are made (headers are undefined, so the useEffect early-returns).
    await screen.findByText("Model Registry");
    expect(apiGetMock).not.toHaveBeenCalled();

    await screen.findByText("No models yet.");
  });

  it("loads and renders model rows when authenticated", async () => {
    authenticatedSession();
    apiGetMock.mockResolvedValue({ models: [modelRecord()] });

    render(<AdminModelsPage />);

    await screen.findByText("Model One");

    expect(apiGetMock).toHaveBeenCalledWith("/admin/models", {
      headers: { Authorization: "Bearer admin-token" },
    });

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

    render(<AdminModelsPage />);
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
          provider_type: "openai_compat",
          model_name: "gpt-two",
          base_url: "https://gateway-two.example/v1",
          enabled: true,
          visibility: "public",
        },
        {
          headers: { Authorization: "Bearer admin-token" },
        },
      );
    });

    await screen.findByText("Model Two");
  });

  it("shows validation errors before create API call", async () => {
    authenticatedSession();
    apiGetMock.mockResolvedValue({ models: [] });

    render(<AdminModelsPage />);
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
        display_name: "Model One Renamed",
        frequency_penalty: 0.25,
      }),
    );

    render(<AdminModelsPage />);
    await screen.findByText("Model One");

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Edit" }));

    const displayNameInput = screen.getByLabelText("display_name");
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
          provider_type: "openai_compat",
          model_name: "gpt-4o-mini",
          base_url: "https://gateway.example/v1",
          enabled: true,
          visibility: "public",
          prompt_template_id: null,
          temperature: null,
          frequency_penalty: 0.25,
          presence_penalty: null,
          tags: null,
          extra_body: null,
          default_params: null,
        }),
        {
          headers: { Authorization: "Bearer admin-token" },
        },
      );
    });

    await screen.findByText("Model One Renamed");
  });

  it("deletes a model when confirmed", async () => {
    authenticatedSession();
    apiGetMock.mockResolvedValue({ models: [modelRecord()] });
    apiDeleteMock.mockResolvedValue(null);
    vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<AdminModelsPage />);
    await screen.findByText("Model One");

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() => {
      expect(apiDeleteMock).toHaveBeenCalledWith(
        "/admin/models/model-1",
        {
          headers: { Authorization: "Bearer admin-token" },
        },
      );
    });

    await waitFor(() => {
      expect(screen.queryByText("Model One")).toBeNull();
    });
  });

  it("enforces mutual exclusivity between clear api_key and new api_key inputs", async () => {
    authenticatedSession();
    apiGetMock.mockResolvedValue({ models: [modelRecord()] });

    render(<AdminModelsPage />);
    await screen.findByText("Model One");

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Edit" }));

    const apiKeyInput = screen.getByLabelText("new api_key (optional)") as HTMLInputElement;
    const clearApiKeyCheckbox = screen.getByLabelText("clear api_key") as HTMLInputElement;

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

    render(<AdminModelsPage />);
    await screen.findByText("Model One");

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Test" }));

    await waitFor(() => {
      expect(apiPostMock).toHaveBeenCalledWith(
        "/admin/models/model-1/test",
        {},
        { headers: { Authorization: "Bearer admin-token" } },
      );
    });
  });
});
