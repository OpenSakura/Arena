// @vitest-environment jsdom

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createTestI18n, TestI18nProvider } from "@/i18n/test-utils";
import AdminBattlePrepopulationRoute from "./AdminBattlePrepopulationRoute";

const useAuthHeadersMock = vi.fn();
const apiGetMock = vi.fn();
const apiPostMock = vi.fn();

vi.mock("@/hooks/useAuthHeaders", () => ({
  useAuthHeaders: () => useAuthHeadersMock(),
}));

vi.mock("@/lib/api", () => ({
  apiGet: (...args: unknown[]) => apiGetMock(...args),
  apiPost: (...args: unknown[]) => apiPostMock(...args),
  isApiUnauthorizedError: (error: unknown) => Boolean(error && typeof error === "object" && "status" in error && error.status === 401),
}));

afterEach(() => {
  vi.restoreAllMocks();
});

beforeEach(() => {
  useAuthHeadersMock.mockReset();
  apiGetMock.mockReset();
  apiPostMock.mockReset();

  useAuthHeadersMock.mockReturnValue({
    authStatus: "unauthenticated",
    csrfToken: null,
    sessionError: null,
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
    model_name: "gpt-one",
    enabled: true,
    visibility: "public",
    ...overrides,
  };
}

function statsRecord(overrides: Record<string, unknown> = {}) {
  return {
    available_admin_count: 4,
    available_recycled_count: 3,
    available_total_count: 7,
    generating_count: 2,
    failed_count: 1,
    voted_consumed_count: 5,
    total_count: 15,
    latest_job: null,
    max_job_size: 50,
    ...overrides,
  };
}

function jobRecord(overrides: Record<string, unknown> = {}) {
  return {
    id: "job-1",
    status: "running",
    requested_count: 10,
    completed_count: 4,
    failed_count: 1,
    model_ids: ["model-1"],
    created_at: "2026-05-26T22:00:00Z",
    started_at: "2026-05-26T22:01:00Z",
    finished_at: null,
    last_error: null,
    ...overrides,
  };
}

function mockSuccessfulLoads({ models = [modelRecord()], stats = statsRecord(), jobs = [] as Array<Record<string, unknown>> } = {}) {
  apiGetMock.mockImplementation((path: string) => {
    if (path === "/admin/battle-prepopulation/model-options") {
      return Promise.resolve({ models });
    }
    if (path === "/admin/battle-prepopulation/stats") {
      return Promise.resolve(stats);
    }
    if (path === "/admin/battle-prepopulation/jobs?limit=20") {
      return Promise.resolve({ jobs });
    }

    throw new Error(`unexpected get path: ${path}`);
  });
}

async function renderAdminBattlePrepopulationRoute() {
  const i18n = await createTestI18n("en");

  return render(
    <TestI18nProvider i18n={i18n}>
      <AdminBattlePrepopulationRoute />
    </TestI18nProvider>,
  );
}

describe("AdminBattlePrepopulationRoute", () => {
  it("does not load stats, jobs, or models while unauthenticated", async () => {
    await renderAdminBattlePrepopulationRoute();

    await screen.findByRole("heading", { name: "Battle Prepopulation" });
    expect(apiGetMock).not.toHaveBeenCalled();
    expect(apiPostMock).not.toHaveBeenCalled();
  });

  it("loads stats and model options when authenticated", async () => {
    authenticatedSession();
    mockSuccessfulLoads({
      stats: statsRecord({
        available_admin_count: 6,
        available_recycled_count: 5,
        available_total_count: 11,
        generating_count: 2,
        failed_count: 1,
        voted_consumed_count: 8,
        total_count: 20,
        max_job_size: 50,
        latest_job: jobRecord({ id: "job-latest", status: "completed" }),
      }),
      models: [
        modelRecord({ id: "model-a", display_name: "Model A" }),
        modelRecord({ id: "model-b", display_name: "Model B" }),
      ],
    });

    await renderAdminBattlePrepopulationRoute();

    await screen.findByText("Available admin battles: 6");
    expect(screen.getByText("Available recycled battles: 5")).toBeDefined();
    expect(screen.getByText("Total available: 11")).toBeDefined();
    expect(screen.getByText("Generating: 2")).toBeDefined();
    expect(screen.getByText("Failed: 1")).toBeDefined();
    expect(screen.getByText("Voted and consumed: 8")).toBeDefined();
    expect(screen.getByText("Total: 20")).toBeDefined();
    expect(screen.getByText("Max job size: 50")).toBeDefined();
    expect(screen.getByText("Latest job: completed")).toBeDefined();
    expect(screen.getAllByRole("option", { name: "Model A" })).toHaveLength(2);
    expect(screen.getAllByRole("option", { name: "Model B" })).toHaveLength(2);
    expect(apiGetMock).toHaveBeenCalledWith("/admin/battle-prepopulation/stats");
    expect(apiGetMock).toHaveBeenCalledWith("/admin/battle-prepopulation/model-options");
    expect(apiGetMock).toHaveBeenCalledWith("/admin/battle-prepopulation/jobs?limit=20");
  });

  it("submits a requested amount to create a prepopulation job", async () => {
    authenticatedSession();
    mockSuccessfulLoads();
    apiPostMock.mockResolvedValue(jobRecord({ id: "job-created", requested_count: 12 }));

    await renderAdminBattlePrepopulationRoute();
    await screen.findByText("Total available: 7");

    const user = userEvent.setup();
    await user.clear(screen.getByLabelText("Battles to generate"));
    await user.type(screen.getByLabelText("Battles to generate"), "12");
    await user.click(screen.getByRole("button", { name: "Prepopulate battles" }));

    await waitFor(() => {
      expect(apiPostMock).toHaveBeenCalledWith(
        "/admin/battle-prepopulation/jobs",
        {
          amount: 12,
          model_ids: [],
        },
      );
    });
    expect(apiPostMock.mock.calls[0]).toHaveLength(2);
    await screen.findByText("job-created");
  });

  it.each(["0", "-1", "51"])("rejects invalid amount %s before submit", async (amount) => {
    authenticatedSession();
    mockSuccessfulLoads();

    await renderAdminBattlePrepopulationRoute();
    await screen.findByText("Total available: 7");

    const user = userEvent.setup();
    await user.clear(screen.getByLabelText("Battles to generate"));
    await user.type(screen.getByLabelText("Battles to generate"), amount);
    await user.click(screen.getByRole("button", { name: "Prepopulate battles" }));

    await screen.findByText("Enter a valid amount.");
    expect(apiPostMock).not.toHaveBeenCalled();
  });

  it("renders job progress returned by the API", async () => {
    authenticatedSession();
    mockSuccessfulLoads({
      jobs: [jobRecord({ id: "job-running", requested_count: 10, completed_count: 4, failed_count: 1 })],
    });

    await renderAdminBattlePrepopulationRoute();

    const jobRow = await screen.findByText("job-running");
    const row = jobRow.closest("tr");
    if (!row) throw new Error("job row not found");
    expect(within(row).getByText("running")).toBeDefined();
    expect(within(row).getByText("4 / 10")).toBeDefined();
    expect(within(row).getByText("1 failed")).toBeDefined();
  });

  it("displays API errors from job submission", async () => {
    authenticatedSession();
    mockSuccessfulLoads();
    apiPostMock.mockRejectedValue(new Error("queue unavailable"));

    await renderAdminBattlePrepopulationRoute();
    await screen.findByText("Total available: 7");

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Prepopulate battles" }));

    await screen.findByText("queue unavailable");
  });

  it("allows submitting without explicit model filters", async () => {
    authenticatedSession();
    mockSuccessfulLoads();
    apiPostMock.mockResolvedValue(jobRecord({ model_ids: [] }));

    await renderAdminBattlePrepopulationRoute();
    await screen.findByText("Total available: 7");

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Prepopulate battles" }));

    await waitFor(() => {
      expect(apiPostMock).toHaveBeenCalledWith(
        "/admin/battle-prepopulation/jobs",
        expect.objectContaining({ model_ids: [] }),
      );
    });
  });

  it("displays API errors from initial load", async () => {
    authenticatedSession();
    apiGetMock.mockRejectedValue(new Error("initial load failed"));

    await renderAdminBattlePrepopulationRoute();
    await screen.findByText("initial load failed");
  });

  it("submits one selected model id", async () => {
    authenticatedSession();
    mockSuccessfulLoads({ models: [modelRecord({ id: "model-a", display_name: "Model Alpha" })] });
    apiPostMock.mockResolvedValue(jobRecord({ model_ids: ["model-a"] }));

    await renderAdminBattlePrepopulationRoute();
    await screen.findByText("Total available: 7");

    const user = userEvent.setup();
    await user.selectOptions(screen.getByLabelText("Model 1"), ["model-a"]);
    await user.click(screen.getByRole("button", { name: "Prepopulate battles" }));

    await waitFor(() => {
      expect(apiPostMock).toHaveBeenCalledWith(
        "/admin/battle-prepopulation/jobs",
        expect.objectContaining({ model_ids: ["model-a"] }),
      );
    });
  });

  it("submits one selected model id if only Model 2 is selected", async () => {
    authenticatedSession();
    mockSuccessfulLoads({ models: [modelRecord({ id: "model-a", display_name: "Model Alpha" })] });
    apiPostMock.mockResolvedValue(jobRecord({ model_ids: ["model-a"] }));

    await renderAdminBattlePrepopulationRoute();
    await screen.findByText("Total available: 7");

    const user = userEvent.setup();
    await user.selectOptions(screen.getByLabelText("Model 2"), ["model-a"]);
    await user.click(screen.getByRole("button", { name: "Prepopulate battles" }));

    await waitFor(() => {
      expect(apiPostMock).toHaveBeenCalledWith(
        "/admin/battle-prepopulation/jobs",
        expect.objectContaining({ model_ids: ["model-a"] }),
      );
    });
  });

  it("submits two selected model ids", async () => {
    authenticatedSession();
    mockSuccessfulLoads({
      models: [
        modelRecord({ id: "model-a", display_name: "Model Alpha" }),
        modelRecord({ id: "model-b", display_name: "Model Beta" }),
      ],
    });
    apiPostMock.mockResolvedValue(jobRecord({ model_ids: ["model-a", "model-b"] }));

    await renderAdminBattlePrepopulationRoute();
    await screen.findByText("Total available: 7");

    const user = userEvent.setup();
    await user.selectOptions(screen.getByLabelText("Model 1"), "model-a");
    await user.selectOptions(screen.getByLabelText("Model 2"), "model-b");
    await user.click(screen.getByRole("button", { name: "Prepopulate battles" }));

    await waitFor(() => {
      expect(apiPostMock).toHaveBeenCalledWith(
        "/admin/battle-prepopulation/jobs",
        expect.objectContaining({ model_ids: ["model-a", "model-b"] }),
      );
    });
  });

  it("rejects duplicate selected models before submit", async () => {
    authenticatedSession();
    mockSuccessfulLoads({
      models: [
        modelRecord({ id: "model-a", display_name: "Model Alpha" }),
      ],
    });

    await renderAdminBattlePrepopulationRoute();
    await screen.findByText("Total available: 7");

    const user = userEvent.setup();
    await user.selectOptions(screen.getByLabelText("Model 1"), "model-a");
    await user.selectOptions(screen.getByLabelText("Model 2"), "model-a");
    await user.click(screen.getByRole("button", { name: "Prepopulate battles" }));

    await screen.findByText("Select zero, one, or two models.");
    expect(apiPostMock).not.toHaveBeenCalled();
  });
});
