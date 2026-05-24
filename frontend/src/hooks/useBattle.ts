/**
 * frontend/src/hooks/useBattle.ts
 *
 * Custom hook encapsulating battle lifecycle state management.
 * Replaces 15+ useState hooks in BattleView with a single useReducer.
 */

import { useEffect, useMemo, useReducer, useRef } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { hasBattleSessionError, isBattleBootstrapReady } from "@/components/battleAuth";
import { loadOrCreateBattle, mergeBattleDelta } from "@/components/battleViewUtils";
import { getApiPrefix, apiPost } from "@/lib/api";
import { streamSSE } from "@/lib/sse";
import { useArenaAuth } from "@/hooks/useArenaAuth";
import { asRecord, isRecord } from "@/lib/typeGuards";
import { SESSION_EXPIRED_MESSAGE } from "@/auth/session";

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
  | "running";
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
  retry_allowed: boolean;
  run_a: RunPublic | null;
  run_b: RunPublic | null;
};

const redirectedBattleCache = new Map<string, BattlePublic>();
const bootstrapRequestCache = new Map<string, Promise<BattlePublic>>();

export function __resetBattleRedirectCacheForTests() {
  redirectedBattleCache.clear();
  bootstrapRequestCache.clear();
}

function getBootstrapBattle(
  bootstrapKey: string,
  battleId: string,
): Promise<BattlePublic> {
  const cached = bootstrapRequestCache.get(bootstrapKey);
  if (cached) {
    return cached;
  }

  const request = loadOrCreateBattle(battleId)
    .then(parseBattlePublic)
    .finally(() => {
      if (bootstrapRequestCache.get(bootstrapKey) === request) {
        bootstrapRequestCache.delete(bootstrapKey);
      }
    });

  bootstrapRequestCache.set(bootstrapKey, request);
  return request;
}

export type RevealData = {
  A: { model_id: string; display_name: string };
  B: { model_id: string; display_name: string };
};

export type VoteSubmitResponse = {
  vote_id: string;
  battle_id: string;
  winner: Winner;
  reveal: RevealData;
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
  submittingVote: boolean;
  voteId: string | null;
  reveal: RevealData | null;
  retryCount: number;
  retryAllowed: boolean;
};

type Action =
  | { type: "RESET_BATTLE" }
  | { type: "BOOTSTRAP_SUCCESS"; battle: BattlePublic }
  | { type: "SYNC_BATTLE_PUBLIC"; battle: BattlePublic }
  | { type: "BOOTSTRAP_ERROR"; error: string }
  | { type: "SET_STATUS"; status: BattleStatus }
  | { type: "SET_ERROR"; error: string }
  | { type: "STREAM_BATTLE_STARTED" }
  | { type: "STREAM_DELTA"; side: Side; text: string; replay: boolean; chunkIndex: number | null }
  | { type: "STREAM_RECONNECTING" }
  | { type: "RUN_ERROR"; error: string | null; side?: Side }
  | { type: "BATTLE_COMPLETED" }
  | { type: "BATTLE_FAILED"; detail?: string | null }
  | { type: "BATTLE_ERROR"; detail: string | null }
  | { type: "STREAM_ENDED_EARLY" }
  | { type: "STREAM_ERROR"; error: string }
  | { type: "SET_WINNER"; winner: Winner | null }
  | { type: "TOGGLE_RUBRIC_TAG"; tag: string }
  | { type: "SET_COMMENT"; comment: string }
  | { type: "VOTE_SUBMITTING" }
  | { type: "VOTE_SUCCESS"; voteId: string }
  | { type: "VOTE_ERROR"; error: string }
  | { type: "REVEAL_SUCCESS"; reveal: RevealData }
  | { type: "RETRY_ERROR"; error: string; status: Extract<BattleStatus, "failed" | "error"> }
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
  submittingVote: false,
  voteId: null,
  reveal: null,
  retryCount: 0,
  retryAllowed: false,
};

const BATTLE_PUBLIC_STATUSES: readonly BattlePublicStatus[] = [
  "pending",
  "running",
  "completed",
  "failed",
];

const VOTE_WINNERS: readonly Winner[] = ["A", "B", "tie"];

function battleReducer(state: BattleState, action: Action): BattleState {
  switch (action.type) {
    case "RESET_BATTLE":
      return {
        ...INITIAL_STATE,
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
        retryAllowed: action.battle.retry_allowed,
      };
    }

    case "SYNC_BATTLE_PUBLIC": {
      const battleStatus =
        action.battle.status === "completed" ? "done" : action.battle.status;
      return {
        ...state,
        resolvedBattleId: action.battle.id,
        jpSource: action.battle.source_text,
        jpSourceLang: (action.battle.source_lang ?? "ja").toUpperCase(),
        targetLang: (action.battle.target_lang ?? "zh").toUpperCase(),
        outA: action.battle.run_a?.output_text ?? state.outA,
        outB: action.battle.run_b?.output_text ?? state.outB,
        status: battleStatus,
        retryAllowed: action.battle.retry_allowed,
      };
    }

    case "BOOTSTRAP_ERROR":
      return {
        ...INITIAL_STATE,
        status: "error",
        errorText: action.error,
        retryAllowed: false,
      };

    case "SET_STATUS":
      return { ...state, status: action.status };

    case "SET_ERROR":
      return { ...state, errorText: action.error };

    case "STREAM_BATTLE_STARTED":
      return {
        ...state,
        outA: "",
        outB: "",
        status: "streaming",
        errorText: null,
        winner: null,
        rubricTags: [],
        comment: "",
        submittingVote: false,
        voteId: null,
        reveal: null,
        retryAllowed: false,
      };

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
      {
        const prefix = action.side ? `Run error (${action.side})` : "Run error";
        const fallback = action.side
          ? `Translation run ${action.side} encountered an error`
          : "A translation run encountered an error";

      return {
        ...state,
        errorText:
          state.errorText ??
            (action.error ? `${prefix}: ${action.error}` : fallback),
      };
      }

    case "BATTLE_COMPLETED":
      return { ...state, status: "done", errorText: null, retryAllowed: false };

    case "BATTLE_FAILED":
      return {
        ...state,
        status: "failed",
        errorText: action.detail
          ? `Battle failed: ${action.detail}`
          : state.errorText ?? "Battle failed to complete",
        retryAllowed: false,
      };

    case "BATTLE_ERROR":
      return {
        ...state,
        status: "error",
        errorText: action.detail ? `Battle error: ${action.detail}` : "Battle stream failed",
        retryAllowed: false,
      };

    case "STREAM_ENDED_EARLY":
      return {
        ...state,
        status:
          state.status === "done" || state.status === "failed"
            ? state.status
            : "error",
        errorText: state.errorText ?? "Battle stream ended before completion",
        retryAllowed: false,
      };

    case "STREAM_ERROR":
      return { ...state, status: "error", errorText: action.error, retryAllowed: false };

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

    case "VOTE_SUBMITTING":
      return { ...state, submittingVote: true, errorText: null };

    case "VOTE_SUCCESS":
      return { ...state, submittingVote: false, voteId: action.voteId };

    case "VOTE_ERROR":
      return { ...state, submittingVote: false, errorText: action.error };

    case "REVEAL_SUCCESS":
      return { ...state, reveal: action.reveal };

    case "RETRY_ERROR":
      return { ...state, status: action.status, errorText: action.error, retryAllowed: state.retryAllowed };

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
        retryCount: state.retryCount + 1,
        retryAllowed: false,
      };

    default:
      return state;
  }
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
    typeof value.retry_allowed === "boolean" &&
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
    isRevealData(value.reveal)
  );
}

function parseVoteSubmitResponse(value: unknown): VoteSubmitResponse {
  if (!isVoteSubmitResponse(value)) {
    throw new Error("Invalid vote response");
  }
  return value;
}

export function useBattle(battleId: string) {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const restartKey = searchParams.get("r") ?? "";
  const { authStatus, sessionError } = useArenaAuth();
  const isAuthed = authStatus === "authenticated";
  const hasSessionError = hasBattleSessionError(sessionError);

  const [state, dispatch] = useReducer(battleReducer, INITIAL_STATE);

  const statusRef = useRef<BattleStatus>(state.status);
  const bootstrapKeyRef = useRef<string | null>(null);
  const replayPolicyRef = useRef<Record<Side, ReplayPolicy>>({ A: "consume", B: "consume" });
  const voteSubmitLockRef = useRef(false);
  const terminalSyncKeyRef = useRef<string | null>(null);

  useEffect(() => {
    statusRef.current = state.status;
  }, [state.status]);

  useEffect(() => {
    let cancelled = false;
    const bootstrapKey = battleId === "new" ? `new:${restartKey}` : `battle:${battleId}`;

    async function bootstrapBattle() {
      if (!isBattleBootstrapReady(authStatus)) {
        return;
      }
      if (battleId === "new") {
        if (hasSessionError) {
          dispatch({ type: "BOOTSTRAP_ERROR", error: SESSION_EXPIRED_MESSAGE });
          return;
        }
        if (!isAuthed) {
          dispatch({ type: "BOOTSTRAP_ERROR", error: "Login required to start a battle." });
          return;
        }
      }

      if (battleId !== "new" && state.resolvedBattleId === battleId) {
        bootstrapKeyRef.current = bootstrapKey;
        return;
      }

      const redirectedBattle = battleId === "new" ? null : redirectedBattleCache.get(battleId) ?? null;
      if (battleId !== "new" && redirectedBattle) {
        redirectedBattleCache.delete(battleId);
        bootstrapKeyRef.current = bootstrapKey;
        if (!isAuthed) {
          dispatch({ type: "BOOTSTRAP_ERROR", error: "Login required to view battles." });
          return;
        }

        const isFinished =
          redirectedBattle.status === "completed" || redirectedBattle.status === "failed";

        replayPolicyRef.current = {
          A:
            isFinished && Boolean(redirectedBattle.run_a?.output_text)
              ? "ignore"
              : "consume",
          B:
            isFinished && Boolean(redirectedBattle.run_b?.output_text)
              ? "ignore"
              : "consume",
        };
        dispatch({ type: "BOOTSTRAP_SUCCESS", battle: redirectedBattle });
        return;
      }

      if (bootstrapKeyRef.current === bootstrapKey && state.resolvedBattleId !== null) {
        return;
      }

      bootstrapKeyRef.current = bootstrapKey;
      dispatch({ type: "RESET_BATTLE" });
      replayPolicyRef.current = { A: "consume", B: "consume" };
      voteSubmitLockRef.current = false;

      try {
        const battle = await getBootstrapBattle(bootstrapKey, battleId);
        if (cancelled) return;

        const isFinished = battle.status === "completed" || battle.status === "failed";

        if (!isAuthed) {
          dispatch({ type: "BOOTSTRAP_ERROR", error: "Login required to view battles." });
          return;
        }

        replayPolicyRef.current = {
          A: isFinished && Boolean(battle.run_a?.output_text) ? "ignore" : "consume",
          B: isFinished && Boolean(battle.run_b?.output_text) ? "ignore" : "consume",
        };

        dispatch({ type: "BOOTSTRAP_SUCCESS", battle });

        if (battle.id !== battleId) {
          redirectedBattleCache.set(battle.id, battle);
          bootstrapKeyRef.current = `battle:${battle.id}`;
          navigate(`/battle/${encodeURIComponent(battle.id)}`);
        }
      } catch (err) {
        if (cancelled) return;
        bootstrapKeyRef.current = null;
        
        const message = err instanceof Error ? err.message : "Failed to load battle";
        let error = message;
        if (message.includes("401")) {
          error = isAuthed ? SESSION_EXPIRED_MESSAGE : "Login required to view battles.";
        } else if (message.includes("403")) {
          error = "Permission denied. You can only view your own battles.";
        }

        dispatch({
          type: "BOOTSTRAP_ERROR",
          error,
        });
      }
    }

    void bootstrapBattle();
    return () => {
      cancelled = true;
    };
  }, [
    authStatus,
    battleId,
    hasSessionError,
    isAuthed,
    restartKey,
    navigate,
  ]);

  const streamUrl = useMemo(() => {
    if (!state.resolvedBattleId) {
      return null;
    }
    if (state.status === "done" || state.status === "failed" || state.status === "error") {
      return null;
    }

    return `${getApiPrefix()}/battles/${encodeURIComponent(state.resolvedBattleId)}/stream`;
  }, [state.resolvedBattleId, state.status]);

  useEffect(() => {
    if (state.status !== "failed" && state.status !== "error") {
      terminalSyncKeyRef.current = null;
      return;
    }

    if (
      !state.resolvedBattleId ||
      state.retryAllowed ||
      !isAuthed ||
      hasSessionError ||
      !isBattleBootstrapReady(authStatus)
    ) {
      return;
    }

    const syncKey = `${state.resolvedBattleId}:${state.status}:${state.retryCount}`;
    if (terminalSyncKeyRef.current === syncKey) {
      return;
    }
    terminalSyncKeyRef.current = syncKey;

    let cancelled = false;

    async function syncTerminalBattle() {
      try {
        const battle = await getBootstrapBattle(`terminal:${syncKey}`, state.resolvedBattleId!);
        if (cancelled) {
          return;
        }
        dispatch({ type: "SYNC_BATTLE_PUBLIC", battle });
      } catch (err) {
        if (cancelled) return;
        const message = err instanceof Error ? err.message : "";
        if (message.includes("401")) {
          dispatch({ type: "BOOTSTRAP_ERROR", error: isAuthed ? SESSION_EXPIRED_MESSAGE : "Login required to view battles." });
        } else if (message.includes("403")) {
          dispatch({ type: "BOOTSTRAP_ERROR", error: "Permission denied. You can only view your own battles." });
        }
        // Otherwise, best-effort sync only. Keep current terminal state if refresh fails.
      }
    }

    void syncTerminalBattle();

    return () => {
      cancelled = true;
    };
  }, [
    authStatus,
    hasSessionError,
    isAuthed,
    state.resolvedBattleId,
    state.retryAllowed,
    state.retryCount,
    state.status,
  ]);

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
          signal: abortController.signal,
        })) {
          if (cancelled) {
            return;
          }

          if (evt.event === "sse.retry") {
            dispatch({ type: "STREAM_RECONNECTING" });
            continue;
          }

          if (evt.event === "battle.started") {
            dispatch({ type: "STREAM_BATTLE_STARTED" });
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
            const side = isSide(errorPayload?.side) ? errorPayload.side : undefined;
            dispatch({
              type: "RUN_ERROR",
              error: typeof errorPayload?.error === "string" ? errorPayload.error : null,
              side,
            });
            continue;
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
  }, [state.resolvedBattleId, state.retryCount, streamUrl]);

  async function handleVoteSubmit() {
    if (hasSessionError) {
      dispatch({ type: "VOTE_ERROR", error: SESSION_EXPIRED_MESSAGE });
      return;
    }
    if (!isAuthed) {
      dispatch({ type: "VOTE_ERROR", error: "Login required to submit a vote." });
      return;
    }
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
        await apiPost(`/battles/${encodeURIComponent(state.resolvedBattleId)}/vote`, payload),
      );
      dispatch({ type: "VOTE_SUCCESS", voteId: result.vote_id });
      dispatch({ type: "REVEAL_SUCCESS", reveal: result.reveal });
    } catch (err) {
      dispatch({
        type: "VOTE_ERROR",
        error: err instanceof Error ? err.message : "Failed to submit vote",
      });
    } finally {
      voteSubmitLockRef.current = false;
    }
  }

  function handleStartAnotherBattle() {
    const nonce = Date.now().toString(36);
    navigate(`/battle/new?r=${nonce}`);
  }

  async function handleRetry() {
    const retryFallbackStatus = state.status === "failed" ? "failed" : "error";

    if (hasSessionError) {
      dispatch({ type: "RETRY_ERROR", error: SESSION_EXPIRED_MESSAGE, status: retryFallbackStatus });
      return;
    }
    if (!isAuthed) {
      dispatch({ type: "RETRY_ERROR", error: "Login required to retry.", status: retryFallbackStatus });
      return;
    }
    if (!state.resolvedBattleId || state.voteId) {
      return;
    }

    try {
      await apiPost(`/battles/${encodeURIComponent(state.resolvedBattleId)}/retry`, {});
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

  const voteSubmitted = state.voteId !== null;

  const canVote =
    isAuthed &&
    !hasSessionError &&
    state.resolvedBattleId !== null &&
    state.winner !== null &&
    state.reveal === null &&
    !state.submittingVote &&
    state.status === "done";

  const canRetry =
    isAuthed &&
    !hasSessionError &&
    state.resolvedBattleId !== null &&
    state.retryAllowed &&
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
                  : state.status.charAt(0).toUpperCase() + state.status.slice(1);

  return {
    state,
    dispatch,
    isAuthed,
    authStatus,
    hasSessionError,
    canVote,
    canRetry,
    voteSubmitted,
    statusLabel,
    handleVoteSubmit,
    handleRetry,
    handleStartAnotherBattle,
  };
}
