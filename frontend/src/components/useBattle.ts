/**
 * frontend/src/components/useBattle.ts
 *
 * Custom hook encapsulating battle lifecycle state management.
 * Replaces 15+ useState hooks in BattleView with a single useReducer.
 */

"use client";

import { useEffect, useMemo, useReducer, useRef } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useSession } from "next-auth/react";

import { apiGet, apiPost, getBackendBaseUrl } from "@/lib/api";
import { streamSSE } from "@/lib/sse";
import { isBattleBootstrapReady } from "@/components/battleAuth";
import {
  asRecord,
  loadOrCreateBattle,
  mergeBattleDelta,
} from "@/components/battleView.utils";

// ── Types ──────────────────────────────────────────────────────────

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
  status: string;
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

// ── State & Actions ────────────────────────────────────────────────

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

function battleReducer(state: BattleState, action: Action): BattleState {
  switch (action.type) {
    case "RESET_BATTLE":
      return {
        ...INITIAL_STATE,
        anonBattleTurnstileRequired: state.anonBattleTurnstileRequired,
        turnstileToken: state.turnstileToken,
      };

    case "BOOTSTRAP_SUCCESS": {
      const b = action.battle;
      return {
        ...state,
        resolvedBattleId: b.id,
        jpSource: b.source_text,
        jpSourceLang: (b.source_lang ?? "ja").toUpperCase(),
        targetLang: (b.target_lang ?? "zh").toUpperCase(),
        outA: b.run_a?.output_text ?? "",
        outB: b.run_b?.output_text ?? "",
        status: b.status === "completed" ? "done" : (b.status as BattleStatus),
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
        errorText: action.detail
          ? `Battle error: ${action.detail}`
          : "Battle stream failed",
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

    case "TOGGLE_RUBRIC_TAG": {
      const tags = state.rubricTags.includes(action.tag)
        ? state.rubricTags.filter((t) => t !== action.tag)
        : [...state.rubricTags, action.tag];
      return { ...state, rubricTags: tags };
    }

    case "SET_COMMENT":
      return { ...state, comment: action.comment };

    case "SET_TURNSTILE_TOKEN":
      return { ...state, turnstileToken: action.token };

    case "VOTE_SUBMITTING":
      return { ...state, submittingVote: true, errorText: null };

    case "VOTE_SUCCESS":
      return { ...state, submittingVote: false, voteId: action.voteId };

    case "VOTE_ERROR":
      return {
        ...state,
        submittingVote: false,
        errorText: action.error,
      };

    case "REVEAL_LOADING":
      return { ...state, revealLoading: true, errorText: null };

    case "REVEAL_SUCCESS":
      return { ...state, revealLoading: false, reveal: action.reveal };

    case "REVEAL_ERROR":
      return { ...state, revealLoading: false, errorText: action.error };

    case "RETRY_ERROR":
      return {
        ...state,
        status: action.status,
        errorText: action.error,
      };

    case "SET_TURNSTILE_REQUIRED":
      return { ...state, anonBattleTurnstileRequired: action.required };

    case "RETRY_BATTLE":
      return {
        ...state,
        outA: "",
        outB: "",
        status: "loading" as BattleStatus,
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

// ── Hook ───────────────────────────────────────────────────────────

export function useBattle(battleId: string) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const restartKey = searchParams.get("r") ?? "";

  const { data: session, status: authStatus } = useSession();
  const accessToken = session?.accessToken;
  const sessionErrorString = session?.error;
  const hasRefreshError = typeof sessionErrorString === "string" && [
    "RefreshTokenMissing",
    "RefreshDiscoveryFailed",
    "RefreshTokenExpired",
    "RefreshTokenError"
  ].includes(sessionErrorString);
  const isAuthed = authStatus === "authenticated" && Boolean(accessToken);
  const accessTokenRef = useRef(accessToken);
  useEffect(() => {
    accessTokenRef.current = accessToken;
  }, [accessToken]);

  const turnstileSiteKey = process.env.NEXT_PUBLIC_TURNSTILE_SITE_KEY ?? "";

  const [state, dispatch] = useReducer(battleReducer, INITIAL_STATE);

  // Refs that mirror state for use inside async closures (avoids stale captures).
  const statusRef = useRef<string>(state.status);
  useEffect(() => {
    statusRef.current = state.status;
  }, [state.status]);

  const replayPolicyRef = useRef<Record<Side, ReplayPolicy>>({
    A: "consume",
    B: "consume",
  });
  const voteSubmitLockRef = useRef(false);

  // ── Effect: load public config ──
  useEffect(() => {
    let cancelled = false;
    async function loadPublicConfig() {
      try {
        const payload = (await apiGet("/public-config")) as Record<
          string,
          unknown
        >;
        if (cancelled) return;
        dispatch({
          type: "SET_TURNSTILE_REQUIRED",
          required: payload?.anon_battle_turnstile_required === true,
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

  // ── Effect: bootstrap battle ──
  useEffect(() => {
    let cancelled = false;

    async function bootstrapBattle() {
      if (!isBattleBootstrapReady(authStatus)) return;

      // Gate: anonymous users creating new battles need Turnstile first.
      // Checked before resolvedBattleId so it activates even on config-driven re-runs.
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

      if (state.resolvedBattleId && state.resolvedBattleId === battleId) return;

      if (battleId === "new" && state.resolvedBattleId) return;

      dispatch({ type: "RESET_BATTLE" });
      replayPolicyRef.current = { A: "consume", B: "consume" };
      voteSubmitLockRef.current = false;

      try {
        const battle = await loadOrCreateBattle<BattlePublic>(
          battleId,
          accessTokenRef.current,
          battleId === "new" ? state.turnstileToken || undefined : undefined,
        );
        if (cancelled) return;

        const isFinished =
          battle.status === "completed" || battle.status === "failed";
        replayPolicyRef.current = {
          A:
            isFinished && Boolean(battle.run_a?.output_text)
              ? "ignore"
              : "consume",
          B:
            isFinished && Boolean(battle.run_b?.output_text)
              ? "ignore"
              : "consume",
        };

        dispatch({ type: "BOOTSTRAP_SUCCESS", battle });

        if (battle.id !== battleId) {
          router.push(`/battle/${encodeURIComponent(battle.id)}`, {
            scroll: false,
          });
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [battleId, authStatus, restartKey, router, state.anonBattleTurnstileRequired]);

  // ── Callback: handle Turnstile token for battle creation ──
  async function handleBattleTurnstileToken(token: string) {
    dispatch({ type: "SET_TURNSTILE_TOKEN", token });

    if (state.status !== "waiting_for_turnstile" || battleId !== "new") return;

    dispatch({ type: "SET_STATUS", status: "loading" });

    try {
      const battle = await loadOrCreateBattle<BattlePublic>(
        "new",
        accessTokenRef.current,
        token,
      );

      replayPolicyRef.current = { A: "consume", B: "consume" };
      dispatch({ type: "BOOTSTRAP_SUCCESS", battle });

      router.push(`/battle/${encodeURIComponent(battle.id)}`, {
        scroll: false,
      });
    } catch (err) {
      dispatch({
        type: "BOOTSTRAP_ERROR",
        error: err instanceof Error ? err.message : "Failed to create battle",
      });
    }
  }

  // ── Effect: SSE stream ──
  const streamUrl = useMemo(() => {
    if (!state.resolvedBattleId) return null;
    return `${getBackendBaseUrl()}/battles/${encodeURIComponent(state.resolvedBattleId)}/stream`;
  }, [state.resolvedBattleId]);

  useEffect(() => {
    if (!streamUrl || !state.resolvedBattleId) return;
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
              ? (statusRef.current as BattleStatus)
              : "streaming",
        });

        for await (const evt of streamSSE(url, {
          headers: accessTokenRef.current
            ? { Authorization: `Bearer ${accessTokenRef.current}` }
            : undefined,
          signal: abortController.signal,
        })) {
          if (cancelled) return;

          if (evt.event === "sse.retry") {
            dispatch({ type: "STREAM_RECONNECTING" });
            continue;
          }

          if (evt.event === "run.delta") {
            const payload = asRecord(evt.data);
            const side = payload?.side;
            const delta = payload?.text_delta;
            const replay = payload?.replay === true;
            const chunkIndexRaw = payload?.chunk_index;
            const chunkIndex =
              typeof chunkIndexRaw === "number" ? chunkIndexRaw : null;

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
              error:
                typeof errorPayload?.error === "string"
                  ? errorPayload.error
                  : null,
            });
          }

          if (evt.event === "battle.error") {
            sawTerminalEvent = true;
            const payload = asRecord(evt.data);
            dispatch({
              type: "BATTLE_ERROR",
              detail:
                typeof payload?.detail === "string" ? payload.detail : null,
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
        console.error("[STREAM ERROR]", err);
        if (cancelled) return;
        const msg = err instanceof Error ? err.message : "Battle stream failed";
        dispatch({
          type: "STREAM_ERROR",
          error: msg.includes("401") 
            ? "Session expired or authentication failed. Please reload the page."
            : msg,
        });
      }
    }

    void runStream();

    return () => {
      cancelled = true;
      abortController.abort();
    };
    // accessToken intentionally excluded — uses ref to avoid SSE reconnect.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [streamUrl, state.resolvedBattleId, state.retryCount]);

  // ── Actions ──

  async function handleVoteSubmit() {
    if (
      voteSubmitLockRef.current ||
      !state.resolvedBattleId ||
      !state.winner ||
      state.reveal
    )
      return;

    voteSubmitLockRef.current = true;
    dispatch({ type: "VOTE_SUBMITTING" });

    try {
      const payload = {
        winner: state.winner,
        rubric: { tags: state.rubricTags },
        comment: state.comment || null,
      };
      const result = (await apiPost(
        `/battles/${encodeURIComponent(state.resolvedBattleId)}/vote`,
        payload,
        {
          headers: accessTokenRef.current
            ? { Authorization: `Bearer ${accessTokenRef.current}` }
            : undefined,
        },
      )) as VoteSubmitResponse;
      dispatch({ type: "VOTE_SUCCESS", voteId: result.vote_id });

      // Immediately reveal after submit to lock the vote and show model identities.
      // If reveal fails, the vote is still recorded and the user can retry reveal.
      dispatch({ type: "REVEAL_LOADING" });
      try {
        const revealResult = (await apiPost(
          `/battles/${encodeURIComponent(state.resolvedBattleId)}/vote/reveal`,
          {},
          {
            headers: accessTokenRef.current
              ? { Authorization: `Bearer ${accessTokenRef.current}` }
              : undefined,
          },
        )) as VoteSubmitResponse;
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
    if (!state.resolvedBattleId || !state.voteId || state.reveal) return;

    dispatch({ type: "REVEAL_LOADING" });

    try {
      const result = (await apiPost(
        `/battles/${encodeURIComponent(state.resolvedBattleId)}/vote/reveal`,
        {},
        {
          headers: accessTokenRef.current
            ? { Authorization: `Bearer ${accessTokenRef.current}` }
            : undefined,
        },
      )) as VoteSubmitResponse;
      if (result.reveal) {
        dispatch({ type: "REVEAL_SUCCESS", reveal: result.reveal });
      } else {
        dispatch({ type: "REVEAL_ERROR", error: "Reveal succeeded but response was missing reveal data" });
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
    if (!state.resolvedBattleId || state.voteId) return;

    const retryFallbackStatus = state.status === "failed" ? "failed" : "error";

    try {
      await apiPost(
        `/battles/${encodeURIComponent(state.resolvedBattleId)}/retry`,
        {},
        {
          headers: accessTokenRef.current
            ? { Authorization: `Bearer ${accessTokenRef.current}` }
            : undefined,
        },
      );
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

  // ── Derived values ──

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
