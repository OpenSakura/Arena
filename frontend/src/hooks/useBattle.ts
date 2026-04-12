/**
 * frontend/src/hooks/useBattle.ts
 *
 * Custom hook encapsulating battle lifecycle state management.
 * Replaces 15+ useState hooks in BattleView with a single useReducer.
 */

"use client";

import { useEffect, useMemo, useReducer, useRef } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { isBattleBootstrapReady } from "@/components/battleAuth";
import {
  asRecord,
  loadOrCreateBattle,
  mergeBattleDelta,
} from "@/components/battleViewUtils";
import { getBackendBaseUrl, apiGet, apiPost } from "@/lib/api";
import { streamSSE } from "@/lib/sse";
import { useAuthHeaders } from "@/hooks/useAuthHeaders";

type Side = "A" | "B";
type ReplayPolicy = "consume" | "ignore";
type BattleStatus =
  | "loading"
  | "streaming"
  | "reconnecting"
  | "done"
  | "failed"
  | "error"
  | "pending"
  | "running"
  | "waiting_for_turnstile";
type Winner = "A" | "B" | "tie";
type BattlePublicStatus = "pending" | "running" | "completed" | "failed";

type RunPublic = {
  id: string;
  side: Side;
  output_text: string | null;
  stats: Record<string, unknown> | null;
  error_text: string | null;
};

type BattlePublic = {
  id: string;
  task_id: string;
  source_text: string;
  source_lang: string;
  target_lang: string;
  mode: string;
  status: BattlePublicStatus;
  run_a: RunPublic | null;
  run_b: RunPublic | null;
};

export type RevealData = {
  A: { model_id: string; display_name: string };
  B: { model_id: string; display_name: string };
};

export type VoteSubmitResponse = {
  vote_id: string;
  battle_id: string;
  winner: Winner;
  reveal: RevealData | null;
};

export type BattleState = {
  resolvedBattleId: string | null;
  jpSource: string;
  jpSourceLang: string;
  targetLang: string;
  outA: string;
  outB: string;
  status: BattleStatus;
  errorText: string | null;
  winner: Winner | null;
  rubricTags: string[];
  comment: string;
  turnstileToken: string;
  submittingVote: boolean;
  voteId: string | null;
  reveal: RevealData | null;
  revealLoading: boolean;
  retryCount: number;
  anonBattleTurnstileRequired: boolean;
};

type Action =
  | { type: "RESET_BATTLE" }
  | { type: "BOOTSTRAP_SUCCESS"; battle: BattlePublic }
  | { type: "BOOTSTRAP_ERROR"; error: string }
  | { type: "SET_STATUS"; status: BattleStatus }
  | { type: "SET_ERROR"; error: string }
  | { type: "STREAM_DELTA"; side: Side; text: string; replay: boolean; chunkIndex: number | null }
  | { type: "STREAM_RECONNECTING" }
  | { type: "RUN_ERROR"; error: string | null }
  | { type: "BATTLE_COMPLETED" }
  | { type: "BATTLE_FAILED"; detail?: string | null }
  | { type: "BATTLE_ERROR"; detail: string | null }
  | { type: "STREAM_ENDED_EARLY" }
  | { type: "STREAM_ERROR"; error: string }
  | { type: "SET_WINNER"; winner: Winner | null }
  | { type: "TOGGLE_RUBRIC_TAG"; tag: string }
  | { type: "SET_COMMENT"; comment: string }
  | { type: "SET_TURNSTILE_TOKEN"; token: string }
  | { type: "VOTE_SUBMITTING" }
  | { type: "VOTE_SUCCESS"; voteId: string }
  | { type: "VOTE_ERROR"; error: string }
  | { type: "REVEAL_LOADING" }
  | { type: "REVEAL_SUCCESS"; reveal: RevealData }
  | { type: "REVEAL_ERROR"; error: string }
  | { type: "RETRY_ERROR"; error: string; status: Extract<BattleStatus, "failed" | "error"> }
  | { type: "SET_TURNSTILE_REQUIRED"; required: boolean }
  | { type: "RETRY_BATTLE" };

const INITIAL_STATE: BattleState = {
  resolvedBattleId: null,
  jpSource: "",
  jpSourceLang: "JA",
  targetLang: "ZH",
  outA: "",
  outB: "",
  status: "loading",
  errorText: null,
  winner: null,
  rubricTags: [],
  comment: "",
  turnstileToken: "",
  submittingVote: false,
  voteId: null,
  reveal: null,
  revealLoading: false,
  retryCount: 0,
  anonBattleTurnstileRequired: false,
};

const BATTLE_PUBLIC_STATUSES: readonly BattlePublicStatus[] = [
  "pending",
  "running",
  "completed",
  "failed",
];

const VOTE_WINNERS: readonly Winner[] = ["A", "B", "tie"];
const REFRESH_ERRORS = [
  "RefreshTokenMissing",
  "RefreshDiscoveryFailed",
  "RefreshTokenExpired",
  "RefreshTokenError",
];

function battleReducer(state: BattleState, action: Action): BattleState {
  switch (action.type) {
    case "RESET_BATTLE":
      return {
        ...INITIAL_STATE,
        anonBattleTurnstileRequired: state.anonBattleTurnstileRequired,
        turnstileToken: state.turnstileToken,
      };

    case "BOOTSTRAP_SUCCESS": {
      const battleStatus =
        action.battle.status === "completed" ? "done" : action.battle.status;
      return {
        ...state,
        resolvedBattleId: action.battle.id,
        jpSource: action.battle.source_text,
        jpSourceLang: (action.battle.source_lang ?? "ja").toUpperCase(),
        targetLang: (action.battle.target_lang ?? "zh").toUpperCase(),
        outA: action.battle.run_a?.output_text ?? "",
        outB: action.battle.run_b?.output_text ?? "",
        status: battleStatus,
        errorText: null,
      };
    }

    case "BOOTSTRAP_ERROR":
      return { ...state, status: "error", errorText: action.error };

    case "SET_STATUS":
      return { ...state, status: action.status };

    case "SET_ERROR":
      return { ...state, errorText: action.error };

    case "STREAM_DELTA": {
      const key = action.side === "A" ? "outA" : "outB";
      return {
        ...state,
        [key]: mergeBattleDelta(
          state[key],
          action.text,
          action.replay,
          action.chunkIndex,
        ),
      };
    }

    case "STREAM_RECONNECTING":
      return {
        ...state,
        status:
          state.status === "done" || state.status === "failed"
            ? state.status
            : "reconnecting",
      };

    case "RUN_ERROR":
      return {
        ...state,
        status: "error",
        errorText:
          state.errorText ??
          (action.error ? `Run error: ${action.error}` : "A translation run encountered an error"),
      };

    case "BATTLE_COMPLETED":
      return { ...state, status: "done" };

    case "BATTLE_FAILED":
      return {
        ...state,
        status: "failed",
        errorText: action.detail
          ? `Battle failed: ${action.detail}`
          : state.errorText ?? "Battle failed to complete",
      };

    case "BATTLE_ERROR":
      return {
        ...state,
        status: "error",
        errorText: action.detail ? `Battle error: ${action.detail}` : "Battle stream failed",
      };

    case "STREAM_ENDED_EARLY":
      return {
        ...state,
        status:
          state.status === "done" || state.status === "failed"
            ? state.status
            : "error",
        errorText: state.errorText ?? "Battle stream ended before completion",
      };

    case "STREAM_ERROR":
      return { ...state, status: "error", errorText: action.error };

    case "SET_WINNER":
      return { ...state, winner: action.winner };

    case "TOGGLE_RUBRIC_TAG":
      return {
        ...state,
        rubricTags: state.rubricTags.includes(action.tag)
          ? state.rubricTags.filter((tag) => tag !== action.tag)
          : [...state.rubricTags, action.tag],
      };

    case "SET_COMMENT":
      return { ...state, comment: action.comment };

    case "SET_TURNSTILE_TOKEN":
      return { ...state, turnstileToken: action.token };

    case "VOTE_SUBMITTING":
      return { ...state, submittingVote: true, errorText: null };

    case "VOTE_SUCCESS":
      return { ...state, submittingVote: false, voteId: action.voteId };

    case "VOTE_ERROR":
      return { ...state, submittingVote: false, errorText: action.error };

    case "REVEAL_LOADING":
      return { ...state, revealLoading: true, errorText: null };

    case "REVEAL_SUCCESS":
      return { ...state, revealLoading: false, reveal: action.reveal };

    case "REVEAL_ERROR":
      return { ...state, revealLoading: false, errorText: action.error };

    case "RETRY_ERROR":
      return { ...state, status: action.status, errorText: action.error };

    case "SET_TURNSTILE_REQUIRED":
      return { ...state, anonBattleTurnstileRequired: action.required };

    case "RETRY_BATTLE":
      return {
        ...state,
        outA: "",
        outB: "",
        status: "loading",
        errorText: null,
        winner: null,
        rubricTags: [],
        comment: "",
        submittingVote: false,
        voteId: null,
        reveal: null,
        revealLoading: false,
        retryCount: state.retryCount + 1,
      };

    default:
      return state;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isSide(value: unknown): value is Side {
  return value === "A" || value === "B";
}

function isWinner(value: unknown): value is Winner {
  return typeof value === "string" && VOTE_WINNERS.includes(value as Winner);
}

function isRunPublic(value: unknown): value is RunPublic {
  if (!isRecord(value)) {
    return false;
  }

  return (
    typeof value.id === "string" &&
    isSide(value.side) &&
    (typeof value.output_text === "string" || value.output_text === null) &&
    (isRecord(value.stats) || value.stats === null) &&
    (typeof value.error_text === "string" || value.error_text === null)
  );
}

function isBattlePublic(value: unknown): value is BattlePublic {
  if (!isRecord(value)) {
    return false;
  }

  return (
    typeof value.id === "string" &&
    typeof value.task_id === "string" &&
    typeof value.source_text === "string" &&
    typeof value.source_lang === "string" &&
    typeof value.target_lang === "string" &&
    typeof value.mode === "string" &&
    typeof value.status === "string" &&
    BATTLE_PUBLIC_STATUSES.includes(value.status as BattlePublicStatus) &&
    (value.run_a === null || isRunPublic(value.run_a)) &&
    (value.run_b === null || isRunPublic(value.run_b))
  );
}

function parseBattlePublic(value: unknown): BattlePublic {
  if (!isBattlePublic(value)) {
    throw new Error("Invalid battle response");
  }
  return value;
}

function isRevealEntry(value: unknown): value is RevealData["A"] {
  return isRecord(value) && typeof value.model_id === "string" && typeof value.display_name === "string";
}

function isRevealData(value: unknown): value is RevealData {
  return isRecord(value) && isRevealEntry(value.A) && isRevealEntry(value.B);
}

function isVoteSubmitResponse(value: unknown): value is VoteSubmitResponse {
  return (
    isRecord(value) &&
    typeof value.vote_id === "string" &&
    typeof value.battle_id === "string" &&
    isWinner(value.winner) &&
    (value.reveal === null || isRevealData(value.reveal))
  );
}

function parseVoteSubmitResponse(value: unknown): VoteSubmitResponse {
  if (!isVoteSubmitResponse(value)) {
    throw new Error("Invalid vote response");
  }
  return value;
}

function parsePublicConfig(value: unknown): { anonBattleTurnstileRequired: boolean } {
  if (!isRecord(value)) {
    throw new Error("Invalid public config response");
  }

  return {
    anonBattleTurnstileRequired: value.anon_battle_turnstile_required === true,
  };
}

export function useBattle(battleId: string) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const restartKey = searchParams.get("r") ?? "";
  const { headers, headersRef, accessTokenRef, authStatus, accessToken, sessionError } = useAuthHeaders();
  const turnstileSiteKey = process.env.NEXT_PUBLIC_TURNSTILE_SITE_KEY ?? "";
  const isAuthed = authStatus === "authenticated" && Boolean(accessToken);
  const hasRefreshError = sessionError !== null && REFRESH_ERRORS.includes(sessionError);

  const [state, dispatch] = useReducer(battleReducer, INITIAL_STATE);

  const statusRef = useRef<BattleStatus>(state.status);
  const replayPolicyRef = useRef<Record<Side, ReplayPolicy>>({ A: "consume", B: "consume" });
  const voteSubmitLockRef = useRef(false);

  useEffect(() => {
    statusRef.current = state.status;
  }, [state.status]);

  useEffect(() => {
    let cancelled = false;

    async function loadPublicConfig() {
      try {
        const payload = parsePublicConfig(await apiGet("/public-config"));
        if (cancelled) return;
        dispatch({
          type: "SET_TURNSTILE_REQUIRED",
          required: payload.anonBattleTurnstileRequired,
        });
      } catch {
        if (cancelled) return;
        dispatch({
          type: "SET_TURNSTILE_REQUIRED",
          required: Boolean(turnstileSiteKey),
        });
      }
    }

    void loadPublicConfig();
    return () => {
      cancelled = true;
    };
  }, [turnstileSiteKey]);

  useEffect(() => {
    let cancelled = false;

    async function bootstrapBattle() {
      if (!isBattleBootstrapReady(authStatus)) {
        return;
      }

      if (
        battleId === "new" &&
        !isAuthed &&
        state.anonBattleTurnstileRequired &&
        !state.turnstileToken &&
        !state.resolvedBattleId
      ) {
        dispatch({ type: "SET_STATUS", status: "waiting_for_turnstile" });
        return;
      }

      if (state.resolvedBattleId && state.resolvedBattleId === battleId) {
        return;
      }

      if (battleId === "new" && state.resolvedBattleId) {
        return;
      }

      dispatch({ type: "RESET_BATTLE" });
      replayPolicyRef.current = { A: "consume", B: "consume" };
      voteSubmitLockRef.current = false;

      try {
        const battle = parseBattlePublic(
          await loadOrCreateBattle(
            battleId,
            accessTokenRef.current,
            battleId === "new" ? state.turnstileToken || undefined : undefined,
          ),
        );
        if (cancelled) return;

        const isFinished = battle.status === "completed" || battle.status === "failed";
        replayPolicyRef.current = {
          A: isFinished && Boolean(battle.run_a?.output_text) ? "ignore" : "consume",
          B: isFinished && Boolean(battle.run_b?.output_text) ? "ignore" : "consume",
        };

        dispatch({ type: "BOOTSTRAP_SUCCESS", battle });

        if (battle.id !== battleId) {
          router.push(`/battle/${encodeURIComponent(battle.id)}`, { scroll: false });
        }
      } catch (err) {
        if (cancelled) return;
        dispatch({
          type: "BOOTSTRAP_ERROR",
          error: err instanceof Error ? err.message : "Failed to load battle",
        });
      }
    }

    void bootstrapBattle();
    return () => {
      cancelled = true;
    };
  }, [
    accessTokenRef,
    authStatus,
    battleId,
    isAuthed,
    restartKey,
    router,
    state.anonBattleTurnstileRequired,
    state.resolvedBattleId,
    state.turnstileToken,
  ]);

  async function handleBattleTurnstileToken(token: string) {
    dispatch({ type: "SET_TURNSTILE_TOKEN", token });

    if (battleId === "new") {
      dispatch({ type: "SET_STATUS", status: "loading" });
    }
  }

  const streamUrl = useMemo(() => {
    if (!state.resolvedBattleId) {
      return null;
    }

    return `${getBackendBaseUrl()}/battles/${encodeURIComponent(state.resolvedBattleId)}/stream`;
  }, [state.resolvedBattleId]);

  useEffect(() => {
    if (!streamUrl || !state.resolvedBattleId) {
      return;
    }

    const url = streamUrl;
    const abortController = new AbortController();
    let cancelled = false;

    async function runStream() {
      const startedFromTerminalState =
        statusRef.current === "done" || statusRef.current === "failed";
      let sawTerminalEvent = false;

      try {
        dispatch({
          type: "SET_STATUS",
          status:
            statusRef.current === "done" || statusRef.current === "failed"
              ? statusRef.current
              : "streaming",
        });

        for await (const evt of streamSSE(url, {
          headers: headersRef.current,
          signal: abortController.signal,
        })) {
          if (cancelled) {
            return;
          }

          if (evt.event === "sse.retry") {
            dispatch({ type: "STREAM_RECONNECTING" });
            continue;
          }

          if (evt.event === "run.delta") {
            const payload = asRecord(evt.data);
            const side = payload?.side;
            const delta = payload?.text_delta;
            const replay = payload?.replay === true;
            const chunkIndex =
              typeof payload?.chunk_index === "number" ? payload.chunk_index : null;

            if (
              replay &&
              (side === "A" || side === "B") &&
              replayPolicyRef.current[side] === "ignore"
            ) {
              continue;
            }

            if ((side === "A" || side === "B") && typeof delta === "string") {
              dispatch({
                type: "STREAM_DELTA",
                side,
                text: delta,
                replay,
                chunkIndex,
              });
            }
          }

          if (evt.event === "run.error") {
            const errorPayload = asRecord(evt.data);
            dispatch({
              type: "RUN_ERROR",
              error: typeof errorPayload?.error === "string" ? errorPayload.error : null,
            });
          }

          if (evt.event === "battle.error") {
            sawTerminalEvent = true;
            const payload = asRecord(evt.data);
            dispatch({
              type: "BATTLE_ERROR",
              detail: typeof payload?.detail === "string" ? payload.detail : null,
            });
            break;
          }

          if (evt.event === "battle.completed") {
            sawTerminalEvent = true;
            dispatch({ type: "BATTLE_COMPLETED" });
            break;
          }

          if (evt.event === "battle.failed") {
            sawTerminalEvent = true;
            const payload = asRecord(evt.data);
            dispatch({
              type: "BATTLE_FAILED",
              detail: typeof payload?.detail === "string" ? payload.detail : null,
            });
            break;
          }
        }

        if (!cancelled && !sawTerminalEvent && !startedFromTerminalState) {
          dispatch({ type: "STREAM_ENDED_EARLY" });
        }
      } catch (err) {
        if (cancelled) {
          return;
        }

        const message = err instanceof Error ? err.message : "Battle stream failed";
        dispatch({
          type: "STREAM_ERROR",
          error: message.includes("401")
            ? "Session expired or authentication failed. Please reload the page."
            : message,
        });
      }
    }

    void runStream();

    return () => {
      cancelled = true;
      abortController.abort();
    };
  }, [headersRef, state.resolvedBattleId, state.retryCount, streamUrl]);

  async function handleVoteSubmit() {
    if (
      voteSubmitLockRef.current ||
      !state.resolvedBattleId ||
      !state.winner ||
      state.reveal
    ) {
      return;
    }

    voteSubmitLockRef.current = true;
    dispatch({ type: "VOTE_SUBMITTING" });

    try {
      const payload = {
        winner: state.winner,
        rubric: { tags: state.rubricTags },
        comment: state.comment || null,
      };
      const result = parseVoteSubmitResponse(
        await apiPost(`/battles/${encodeURIComponent(state.resolvedBattleId)}/vote`, payload, {
          headers: headersRef.current,
        }),
      );
      dispatch({ type: "VOTE_SUCCESS", voteId: result.vote_id });

      dispatch({ type: "REVEAL_LOADING" });
      try {
        const revealResult = parseVoteSubmitResponse(
          await apiPost(
            `/battles/${encodeURIComponent(state.resolvedBattleId)}/vote/reveal`,
            {},
            { headers: headersRef.current },
          ),
        );
        if (revealResult.reveal) {
          dispatch({ type: "REVEAL_SUCCESS", reveal: revealResult.reveal });
        } else {
          dispatch({
            type: "REVEAL_ERROR",
            error: "Reveal succeeded but response was missing reveal data",
          });
        }
      } catch (err) {
        dispatch({
          type: "REVEAL_ERROR",
          error: err instanceof Error ? err.message : "Failed to reveal models",
        });
      }
    } catch (err) {
      dispatch({
        type: "VOTE_ERROR",
        error: err instanceof Error ? err.message : "Failed to submit vote",
      });
    } finally {
      voteSubmitLockRef.current = false;
    }
  }

  async function handleReveal() {
    if (!state.resolvedBattleId || !state.voteId || state.reveal) {
      return;
    }

    dispatch({ type: "REVEAL_LOADING" });

    try {
      const result = parseVoteSubmitResponse(
        await apiPost(`/battles/${encodeURIComponent(state.resolvedBattleId)}/vote/reveal`, {}, {
          headers: headersRef.current,
        }),
      );
      if (result.reveal) {
        dispatch({ type: "REVEAL_SUCCESS", reveal: result.reveal });
      } else {
        dispatch({
          type: "REVEAL_ERROR",
          error: "Reveal succeeded but response was missing reveal data",
        });
      }
    } catch (err) {
      dispatch({
        type: "REVEAL_ERROR",
        error: err instanceof Error ? err.message : "Failed to reveal models",
      });
    }
  }

  function handleStartAnotherBattle() {
    const nonce = Date.now().toString(36);
    router.push(`/battle/new?r=${nonce}`);
  }

  async function handleRetry() {
    if (!state.resolvedBattleId || state.voteId) {
      return;
    }

    const retryFallbackStatus = state.status === "failed" ? "failed" : "error";

    try {
      await apiPost(`/battles/${encodeURIComponent(state.resolvedBattleId)}/retry`, {}, {
        headers: headersRef.current,
      });
      replayPolicyRef.current = { A: "consume", B: "consume" };
      voteSubmitLockRef.current = false;
      dispatch({ type: "RETRY_BATTLE" });
    } catch (err) {
      dispatch({
        type: "RETRY_ERROR",
        status: retryFallbackStatus,
        error: err instanceof Error ? err.message : "Failed to retry battle",
      });
    }
  }

  const needsTurnstileForBattle =
    authStatus !== "loading" && !isAuthed && state.anonBattleTurnstileRequired;
  const turnstileMisconfigured = needsTurnstileForBattle && !turnstileSiteKey;
  const voteSubmitted = state.voteId !== null;

  const canVote =
    !hasRefreshError &&
    state.resolvedBattleId !== null &&
    authStatus !== "loading" &&
    state.winner !== null &&
    state.reveal === null &&
    !state.submittingVote &&
    state.status === "done";

  const canReveal =
    !hasRefreshError &&
    state.voteId !== null &&
    state.reveal === null &&
    !state.revealLoading &&
    !state.submittingVote;

  const canRetry =
    !hasRefreshError &&
    state.resolvedBattleId !== null &&
    (state.status === "failed" || state.status === "error") &&
    !state.voteId;

  const statusLabel =
    state.status === "done"
      ? "Complete"
      : state.status === "streaming"
        ? "Translating..."
        : state.status === "reconnecting"
          ? "Reconnecting..."
          : state.status === "failed"
            ? "Failed"
            : state.status === "error"
              ? "Error"
              : state.status === "loading"
                ? "Loading..."
                : state.status === "waiting_for_turnstile"
                  ? "Verification Required"
                  : state.status.charAt(0).toUpperCase() + state.status.slice(1);

  return {
    state,
    dispatch,
    isAuthed,
    authStatus,
    turnstileSiteKey,
    needsTurnstileForBattle,
    turnstileMisconfigured,
    canVote,
    canReveal,
    canRetry,
    voteSubmitted,
    statusLabel,
    handleVoteSubmit,
    handleReveal,
    handleRetry,
    handleBattleTurnstileToken,
    handleStartAnotherBattle,
  };
}
