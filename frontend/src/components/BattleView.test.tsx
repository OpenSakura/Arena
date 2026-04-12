// @vitest-environment jsdom

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { BattleView } from "./BattleView";

const pushMock = vi.fn();
const replaceMock = vi.fn();
const routerMock = { push: pushMock, replace: replaceMock };
const useSearchParamsMock = vi.fn();
const useSessionMock = vi.fn();
const apiGetMock = vi.fn();
const apiPostMock = vi.fn();
const getBackendBaseUrlMock = vi.fn();
const streamSSEMock = vi.fn();
const loadOrCreateBattleMock = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => routerMock,
  useSearchParams: () => useSearchParamsMock(),
}));

vi.mock("next-auth/react", () => ({
  useSession: () => useSessionMock(),
}));

vi.mock("@/lib/api", () => ({
  apiGet: (...args: unknown[]) => apiGetMock(...args),
  apiPost: (...args: unknown[]) => apiPostMock(...args),
  getBackendBaseUrl: () => getBackendBaseUrlMock(),
}));

vi.mock("@/lib/sse", () => ({
  streamSSE: (...args: unknown[]) => streamSSEMock(...args),
}));

vi.mock("@/components/TurnstileWidget", () => ({
  TurnstileWidget: ({ onToken }: { onToken: (token: string) => void }) => (
    <button type="button" onClick={() => onToken("turnstile-token")}>
      Solve Turnstile
    </button>
  ),
}));

vi.mock("@/components/battleViewUtils", () => ({
  loadOrCreateBattle: (...args: unknown[]) => loadOrCreateBattleMock(...args),
  asRecord: (value: unknown) => {
    if (!value || typeof value !== "object") return null;
    return value as Record<string, unknown>;
  },
  mergeBattleDelta: (previous: string, delta: string, replay: boolean, chunkIndex: number | null) => {
    if (replay && (chunkIndex === null || chunkIndex === 0)) {
      return delta;
    }
    return previous + delta;
  },
}));

function emptyEventStream() {
  return (async function* () {
    return;
  })();
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.clearAllMocks();
  delete process.env.NEXT_PUBLIC_TURNSTILE_SITE_KEY;
});

beforeEach(() => {
  pushMock.mockReset();
  replaceMock.mockReset();
  useSearchParamsMock.mockReset();
  useSessionMock.mockReset();
  apiGetMock.mockReset();
  apiPostMock.mockReset();
  getBackendBaseUrlMock.mockReset();
  streamSSEMock.mockReset();
  loadOrCreateBattleMock.mockReset();

  useSearchParamsMock.mockReturnValue({ get: () => null });
  apiGetMock.mockResolvedValue({ anon_battle_turnstile_required: false });
  getBackendBaseUrlMock.mockReturnValue("http://backend.test");
  streamSSEMock.mockReturnValue(emptyEventStream());
});

describe("BattleView", () => {
  it("loads, streams, submits vote, and shows reveal", async () => {
    useSessionMock.mockReturnValue({
      data: { accessToken: "access-token" },
      status: "authenticated",
    });

    loadOrCreateBattleMock.mockResolvedValue({
      id: "battle-123",
      task_id: "task-1",
      source_text: "JP source",
      source_lang: "ja",
      target_lang: "zh",
      mode: "jp2zh_ab",
      status: "pending",
      run_a: { id: "run-a", side: "A", output_text: null, stats: null, error_text: null },
      run_b: { id: "run-b", side: "B", output_text: null, stats: null, error_text: null },
    });

    streamSSEMock.mockReturnValue(
      (async function* () {
        yield { event: "run.delta", data: { side: "A", text_delta: "Alpha" } };
        yield { event: "run.delta", data: { side: "B", text_delta: "Beta" } };
        yield { event: "battle.completed", data: {} };
      })(),
    );

    apiPostMock.mockResolvedValue({
      vote_id: "vote-1",
      battle_id: "battle-123",
      winner: "A",
      reveal: {
        A: { model_id: "model-a", display_name: "Model A" },
        B: { model_id: "model-b", display_name: "Model B" },
      },
    });

    render(<BattleView battleId="new" />);

    await screen.findByText("JP source");
    expect(loadOrCreateBattleMock).toHaveBeenCalledWith("new", "access-token", undefined);

    await screen.findByText("Alpha");
    await screen.findByText("Beta");

    await waitFor(() => {
      expect(screen.getByText(/complete/i)).toBeDefined();
    });

    const user = userEvent.setup();
    let btn = screen.getByText(/Model A is better/i).closest('button');
    if (btn) await user.click(btn);
    btn = screen.getByText(/Submit Vote/i).closest('button');
    if (btn) await user.click(btn);

    await waitFor(() => {
      expect(apiPostMock).toHaveBeenCalledWith(
        "/battles/battle-123/vote",
        {
          winner: "A",
          rubric: { tags: [] },
          comment: null,
        },
        {
          headers: { Authorization: "Bearer access-token" },
        },
      );
    });

    await waitFor(() => {
      const spans = screen.getAllByText("Model A");
      // The panel title is 'Model A', and the reveal badge is 'Model A'
      expect(spans.length).toBeGreaterThanOrEqual(1);
    });
  });

  it("shows backend battle.error details from the stream", async () => {
    useSessionMock.mockReturnValue({
      data: { accessToken: "access-token" },
      status: "authenticated",
    });

    loadOrCreateBattleMock.mockResolvedValue({
      id: "battle-error",
      task_id: "task-error",
      source_text: "JP source",
      source_lang: "ja",
      target_lang: "zh",
      mode: "jp2zh_ab",
      status: "pending",
      run_a: { id: "run-a", side: "A", output_text: null, stats: null, error_text: null },
      run_b: { id: "run-b", side: "B", output_text: null, stats: null, error_text: null },
    });

    streamSSEMock.mockReturnValue(
      (async function* () {
        yield { event: "battle.error", data: { detail: "not_found" } };
      })(),
    );

    render(<BattleView battleId="battle-error" />);

    await screen.findByText("JP source");
    await waitFor(() => {
      expect(screen.getByText("Battle error: not_found")).toBeDefined();
    });
  });

  it("shows fallback error when stream closes without a terminal event", async () => {
    useSessionMock.mockReturnValue({
      data: { accessToken: "access-token" },
      status: "authenticated",
    });

    loadOrCreateBattleMock.mockResolvedValue({
      id: "battle-eof",
      task_id: "task-eof",
      source_text: "JP source",
      source_lang: "ja",
      target_lang: "zh",
      mode: "jp2zh_ab",
      status: "pending",
      run_a: { id: "run-a", side: "A", output_text: null, stats: null, error_text: null },
      run_b: { id: "run-b", side: "B", output_text: null, stats: null, error_text: null },
    });

    streamSSEMock.mockReturnValue(emptyEventStream());

    render(<BattleView battleId="battle-eof" />);

    await screen.findByText("JP source");
    await waitFor(() => {
      expect(screen.getByText("Battle stream ended before completion")).toBeDefined();
    });
  });

  it("requires Turnstile verification for anonymous battle creation", async () => {
    process.env.NEXT_PUBLIC_TURNSTILE_SITE_KEY = "site-key";
    apiGetMock.mockResolvedValue({ anon_battle_turnstile_required: true });

    useSessionMock.mockReturnValue({
      data: null,
      status: "unauthenticated",
    });

    // Delay battle creation so config loads first and Turnstile gate activates
    loadOrCreateBattleMock.mockImplementation(
      () => new Promise(() => {}), // never resolves — gate should prevent reaching this
    );

    render(<BattleView battleId="new" />);

    // Should show verification gate
    await screen.findByText("Verification Required");

    // After solving Turnstile, battle should be created with the token
    loadOrCreateBattleMock.mockResolvedValue({
      id: "battle-2",
      task_id: "task-2",
      source_text: "Another source",
      source_lang: "ja",
      target_lang: "zh",
      mode: "jp2zh_ab",
      status: "pending",
      run_a: { id: "run-a", side: "A", output_text: null, stats: null, error_text: null },
      run_b: { id: "run-b", side: "B", output_text: null, stats: null, error_text: null },
    });

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Solve Turnstile" }));

    await waitFor(() => {
      expect(loadOrCreateBattleMock).toHaveBeenCalledWith("new", undefined, "turnstile-token");
    });
  });

  it("shows misconfiguration warning when backend requires Turnstile but site key is missing", async () => {
    // No NEXT_PUBLIC_TURNSTILE_SITE_KEY set
    apiGetMock.mockResolvedValue({ anon_battle_turnstile_required: true });

    useSessionMock.mockReturnValue({
      data: null,
      status: "unauthenticated",
    });

    loadOrCreateBattleMock.mockImplementation(
      () => new Promise(() => {}),
    );

    render(<BattleView battleId="new" />);

    await screen.findByText("Verification Required");
    expect(
      screen.getByText(/NEXT_PUBLIC_TURNSTILE_SITE_KEY/),
    ).toBeDefined();
  });

  it("shows an error when bootstrap fails", async () => {
    useSessionMock.mockReturnValue({
      data: { accessToken: "access-token" },
      status: "authenticated",
    });
    loadOrCreateBattleMock.mockRejectedValue(new Error("bootstrap failed"));

    render(<BattleView battleId="new" />);

    await screen.findByText("bootstrap failed");
    expect(streamSSEMock).not.toHaveBeenCalled();
  });

  it("keeps persisted completed output when replay starts", async () => {
    useSessionMock.mockReturnValue({
      data: { accessToken: "access-token" },
      status: "authenticated",
    });

    loadOrCreateBattleMock.mockResolvedValue({
      id: "battle-4",
      task_id: "task-4",
      source_text: "source",
      source_lang: "ja",
      target_lang: "zh",
      mode: "jp2zh_ab",
      status: "completed",
      run_a: {
        id: "run-a",
        side: "A",
        output_text: "Persisted complete output",
        stats: null,
        error_text: null,
      },
      run_b: { id: "run-b", side: "B", output_text: "B out", stats: null, error_text: null },
    });

    streamSSEMock.mockReturnValue(
      (async function* () {
        yield {
          event: "run.delta",
          data: { side: "A", text_delta: "TRUNCATED_REPLAY", replay: true, chunk_index: 0 },
        };
      })(),
    );

    render(<BattleView battleId="battle-4" />);

    await screen.findByText("Persisted complete output");
    await waitFor(() => {
      expect(document.body.textContent ?? "").not.toContain("TRUNCATED_REPLAY");
    });
  });

  it("navigates to a fresh battle when start another battle is clicked", async () => {
    useSessionMock.mockReturnValue({
      data: { accessToken: "access-token" },
      status: "authenticated",
    });

    loadOrCreateBattleMock.mockResolvedValue({
      id: "battle-3",
      task_id: "task-3",
      source_text: "source",
      source_lang: "ja",
      target_lang: "zh",
      mode: "jp2zh_ab",
      status: "completed",
      run_a: { id: "run-a", side: "A", output_text: null, stats: null, error_text: null },
      run_b: { id: "run-b", side: "B", output_text: null, stats: null, error_text: null },
    });

    streamSSEMock.mockReturnValue(
      (async function* () {
        yield { event: "battle.failed", data: {} };
      })(),
    );

    render(<BattleView battleId="battle-3" />);

    await waitFor(() => {
      expect(screen.getAllByText(/failed/i).length).toBeGreaterThan(0);
    });

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Start another battle" }));

    expect(pushMock).toHaveBeenCalledWith(expect.stringMatching(/^\/battle\/new\?r=.+/));
  });

  it("keeps retry controls visible when a retry request fails", async () => {
    useSessionMock.mockReturnValue({
      data: { accessToken: "access-token" },
      status: "authenticated",
    });

    loadOrCreateBattleMock.mockResolvedValue({
      id: "battle-retry",
      task_id: "task-retry",
      source_text: "source",
      source_lang: "ja",
      target_lang: "zh",
      mode: "jp2zh_ab",
      status: "pending",
      run_a: { id: "run-a", side: "A", output_text: null, stats: null, error_text: null },
      run_b: { id: "run-b", side: "B", output_text: null, stats: null, error_text: null },
    });

    streamSSEMock.mockReturnValue(
      (async function* () {
        yield { event: "battle.failed", data: {} };
      })(),
    );
    apiPostMock.mockRejectedValue(new Error("retry failed"));

    render(<BattleView battleId="battle-retry" />);

    await screen.findAllByText(/failed/i);

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Retry Battle" }));

    await screen.findByText("retry failed");
    expect(screen.getByRole("button", { name: "Retry Battle" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Start another battle" })).toBeDefined();
  });

  it("resets UI and re-streams after a successful retry", async () => {
    useSessionMock.mockReturnValue({
      data: { accessToken: "access-token" },
      status: "authenticated",
    });

    loadOrCreateBattleMock.mockResolvedValue({
      id: "battle-retry-ok",
      task_id: "task-retry-ok",
      source_text: "source",
      source_lang: "ja",
      target_lang: "zh",
      mode: "jp2zh_ab",
      status: "pending",
      run_a: { id: "run-a", side: "A", output_text: null, stats: null, error_text: null },
      run_b: { id: "run-b", side: "B", output_text: null, stats: null, error_text: null },
    });

    streamSSEMock.mockReturnValueOnce(
      (async function* () {
        yield { event: "battle.failed", data: {} };
      })(),
    );
    apiPostMock.mockResolvedValue({});

    streamSSEMock.mockReturnValueOnce(
      (async function* () {
        yield { event: "run.delta", data: { side: "A", text_delta: "Retried Alpha" } };
        yield { event: "run.delta", data: { side: "B", text_delta: "Retried Beta" } };
        yield { event: "battle.completed", data: {} };
      })(),
    );

    render(<BattleView battleId="battle-retry-ok" />);

    await screen.findAllByText(/failed/i);

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Retry Battle" }));

    await waitFor(() => {
      expect(apiPostMock).toHaveBeenCalledWith(
        "/battles/battle-retry-ok/retry",
        {},
        { headers: { Authorization: "Bearer access-token" } },
      );
    });

    await screen.findByText("Retried Alpha");
    await screen.findByText("Retried Beta");

    await waitFor(() => {
      expect(screen.getByText(/complete/i)).toBeDefined();
    });
  });

  it("hides vote controls before completion and handles missing reveal data", async () => {
    useSessionMock.mockReturnValue({
      data: { accessToken: "access-token" },
      status: "authenticated",
    });

    loadOrCreateBattleMock.mockResolvedValue({
      id: "battle-hidden",
      task_id: "task-1",
      source_text: "JP source",
      source_lang: "ja",
      target_lang: "zh",
      mode: "jp2zh_ab",
      status: "running",
      run_a: { id: "run-a", side: "A", output_text: null, stats: null, error_text: null },
      run_b: { id: "run-b", side: "B", output_text: null, stats: null, error_text: null },
    });

    streamSSEMock.mockReturnValue(
      (async function* () {
        yield { event: "run.delta", data: { side: "A", text_delta: "Alpha" } };
        yield { event: "run.delta", data: { side: "B", text_delta: "Beta" } };
        await new Promise((resolve) => setTimeout(resolve, 50));
        yield { event: "battle.completed", data: {} };
      })(),
    );

    apiPostMock.mockResolvedValue({
      vote_id: "vote-1",
      battle_id: "battle-hidden",
      winner: "A",
      reveal: null,
    });

    render(<BattleView battleId="battle-hidden" />);

    expect(screen.queryByText(/Cast Your Vote/i)).toBeNull();

    await screen.findByText("Alpha");
    await screen.findByText("Beta");

    await waitFor(() => {
      expect(screen.getByText(/Cast Your Vote/i)).toBeDefined();
    });

    const user = userEvent.setup();
    let btn = screen.getByText(/Model A is better/i).closest('button');
    if (btn) await user.click(btn);
    btn = screen.getByText(/Submit Vote/i).closest('button');
    if (btn) await user.click(btn);

    await waitFor(() => {
      expect(screen.getByText(/Reveal succeeded but response was missing reveal data/i)).toBeDefined();
    });
  });

  it("blocks interactions when session refresh has failed", async () => {
    useSessionMock.mockReturnValue({
      data: { accessToken: "access-token", error: "RefreshTokenExpired" },
      status: "authenticated",
    });

    loadOrCreateBattleMock.mockResolvedValue({
      id: "battle-refresh-fail",
      task_id: "task-1",
      source_text: "JP source",
      source_lang: "ja",
      target_lang: "zh",
      mode: "jp2zh_ab",
      status: "completed",
      run_a: { id: "run-a", side: "A", output_text: "Output A", stats: null, error_text: null },
      run_b: { id: "run-b", side: "B", output_text: "Output B", stats: null, error_text: null },
    });

    streamSSEMock.mockReturnValue(emptyEventStream());

    render(<BattleView battleId="battle-refresh-fail" />);

    await screen.findByText("Output A");
    await screen.findByText("Output B");

    await waitFor(() => {
      expect(screen.getByText(/Cast Your Vote/i)).toBeDefined();
    });

    const user = userEvent.setup();
    let btn = screen.getByText(/Model A is better/i).closest('button');
    if (btn) await user.click(btn);

    const submitBtn = screen.getByText(/Submit Vote/i).closest('button');
    expect(submitBtn).toBeDefined();
    expect(submitBtn?.disabled).toBe(true);
  });
});
