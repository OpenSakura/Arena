// @vitest-environment jsdom

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AdminPromptsPage from "./page";

const useSessionMock = vi.fn();
const signInMock = vi.fn();
const apiGetMock = vi.fn();
const apiPostMock = vi.fn();
const apiDeleteMock = vi.fn();

vi.mock("next-auth/react", () => ({
  signIn: (...args: unknown[]) => signInMock(...args),
  useSession: () => useSessionMock(),
}));

vi.mock("@/lib/api", () => ({
  apiGet: (...args: unknown[]) => apiGetMock(...args),
  apiPost: (...args: unknown[]) => apiPostMock(...args),
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
  apiDeleteMock.mockReset();
});

function authenticatedSession() {
  useSessionMock.mockReturnValue({
    data: { accessToken: "admin-token" },
    status: "authenticated",
  });
}

function templateRecord(overrides: Record<string, unknown> = {}) {
  return {
    id: "prompt-1",
    name: "jp2zh_v1",
    version: 3,
    template_text: "Translate",
    input_schema: null,
    content_hash: "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    created_at: "2026-02-18T00:00:00Z",
    ...overrides,
  };
}

describe("AdminPromptsPage", () => {
  it("shows login UI and starts Authentik sign-in when not authenticated", async () => {
    useSessionMock.mockReturnValue({ data: null, status: "unauthenticated" });

    render(<AdminPromptsPage />);

    await screen.findByText("Admin login required");
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Login" }));

    expect(signInMock).toHaveBeenCalledWith("authentik");
    expect(apiGetMock).not.toHaveBeenCalled();
  });

  it("fetches and renders prompt template rows for authenticated users", async () => {
    authenticatedSession();
    apiGetMock.mockResolvedValue({ prompt_templates: [templateRecord()] });

    render(<AdminPromptsPage />);

    await screen.findByText("jp2zh_v1");
    expect(screen.getByText("v3")).toBeDefined();
    expect(apiGetMock).toHaveBeenCalledWith("/admin/prompt-templates", {
      headers: { Authorization: "Bearer admin-token" },
    });
  });

  it("creates a new prompt template and prepends it", async () => {
    authenticatedSession();
    apiGetMock.mockResolvedValue({ prompt_templates: [] });
    apiPostMock.mockResolvedValue(templateRecord({ id: "prompt-2", name: "jp2zh_v2", version: 1 }));

    render(<AdminPromptsPage />);
    await screen.findByText("No prompt templates yet.");

    const user = userEvent.setup();
    await user.type(screen.getByLabelText("name"), "jp2zh_v2");
    await user.type(screen.getByLabelText("template_text"), "You are a translator.");
    fireEvent.change(screen.getByLabelText("input_schema (optional JSON object)"), {
      target: { value: '{"type":"object"}' },
    });
    await user.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(apiPostMock).toHaveBeenCalledWith(
        "/admin/prompt-templates",
        {
          name: "jp2zh_v2",
          template_text: "You are a translator.",
          input_schema: { type: "object" },
        },
        { headers: { Authorization: "Bearer admin-token" } },
      );
    });

    await screen.findByText("jp2zh_v2");
  });

  it("surfaces JSON object validation errors before create", async () => {
    authenticatedSession();
    apiGetMock.mockResolvedValue({ prompt_templates: [] });

    render(<AdminPromptsPage />);
    await screen.findByText("No prompt templates yet.");

    const user = userEvent.setup();
    await user.type(screen.getByLabelText("name"), "jp2zh_v2");
    await user.type(screen.getByLabelText("template_text"), "You are a translator.");
    fireEvent.change(screen.getByLabelText("input_schema (optional JSON object)"), {
      target: { value: "[]" },
    });
    await user.click(screen.getByRole("button", { name: "Create" }));

    await screen.findByText("Expected a JSON object");
    expect(apiPostMock).not.toHaveBeenCalled();
  });

  it("deletes templates after confirmation", async () => {
    authenticatedSession();
    apiGetMock.mockResolvedValue({ prompt_templates: [templateRecord()] });
    apiDeleteMock.mockResolvedValue(null);
    vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<AdminPromptsPage />);
    await screen.findByText("jp2zh_v1");

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() => {
      expect(apiDeleteMock).toHaveBeenCalledWith(
        "/admin/prompt-templates/prompt-1",
        {
          headers: { Authorization: "Bearer admin-token" },
        },
      );
    });

    await waitFor(() => {
      expect(screen.queryByText("jp2zh_v1")).toBeNull();
    });
  });

  it("shows delete API errors", async () => {
    authenticatedSession();
    apiGetMock.mockResolvedValue({ prompt_templates: [templateRecord()] });
    apiDeleteMock.mockRejectedValue(
      new Error("DELETE /admin/prompt-templates/prompt-1 failed: 409 - template in use"),
    );
    vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<AdminPromptsPage />);
    await screen.findByText("jp2zh_v1");

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Delete" }));

    await screen.findByText(/DELETE \/admin\/prompt-templates\/prompt-1 failed: 409/);
  });
});
