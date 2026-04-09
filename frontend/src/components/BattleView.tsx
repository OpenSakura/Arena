/**
 * frontend/src/components/BattleView.tsx
 *
 * Battle UI component with Arena Styling.
 */

"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useSession } from "next-auth/react";
import { useEffect, useMemo, useRef, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";

import { apiGet, apiPost, getBackendBaseUrl } from "@/lib/api";
import { streamSSE } from "@/lib/sse";
import { TurnstileWidget } from "@/components/TurnstileWidget";
import { isBattleBootstrapReady } from "@/components/battleAuth";
import { asRecord, loadOrCreateBattle, mergeBattleDelta } from "@/components/battleView.utils";
import { Button } from "@/components/ui/button";

type Side = "A" | "B";
type ReplayPolicy = "consume" | "ignore";

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

type VoteSubmitResponse = {
  vote_id: string;
  battle_id: string;
  winner: "A" | "B" | "tie";
  reveal: {
    A: { model_id: string; display_name: string };
    B: { model_id: string; display_name: string };
  };
};

const RUBRIC_TAGS = [
  "accuracy",
  "fluency",
  "style",
  "consistency",
  "naturalness",
] as const;

const RUBRIC_ICONS: Record<string, string> = {
  accuracy: "Accuracy",
  fluency: "Fluency",
  style: "Style",
  consistency: "Consistency",
  naturalness: "Naturalness",
};

export function BattleView({ battleId }: { battleId: string }) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const restartKey = searchParams.get("r") ?? "";

  const { data: session, status: authStatus } = useSession();
  const accessToken = session?.accessToken;
  const isAuthed = authStatus === "authenticated" && Boolean(accessToken);
  const accessTokenRef = useRef(accessToken);
  useEffect(() => { accessTokenRef.current = accessToken; }, [accessToken]);
  const turnstileSiteKey = process.env.NEXT_PUBLIC_TURNSTILE_SITE_KEY ?? "";

  const [anonVoteTurnstileRequired, setAnonVoteTurnstileRequired] = useState<boolean>(false);

  const [resolvedBattleId, setResolvedBattleId] = useState<string | null>(null);
  const [jpSource, setJpSource] = useState<string>("");
  const [jpSourceLang, setJpSourceLang] = useState<string>("JA");
  const [targetLang, setTargetLang] = useState<string>("ZH");
  const [outA, setOutA] = useState<string>("");
  const [outB, setOutB] = useState<string>("");
  const [status, setStatus] = useState<string>("loading");
  const statusRef = useRef<string>("loading");
  const [errorText, setErrorText] = useState<string | null>(null);

  const [winner, setWinner] = useState<"A" | "B" | "tie" | null>(null);
  const [rubricTags, setRubricTags] = useState<string[]>([]);
  const [comment, setComment] = useState<string>("");
  const [turnstileToken, setTurnstileToken] = useState<string>("");
  const [submittingVote, setSubmittingVote] = useState<boolean>(false);
  const [reveal, setReveal] = useState<VoteSubmitResponse["reveal"] | null>(null);
  const replayPolicyRef = useRef<Record<Side, ReplayPolicy>>({ A: "consume", B: "consume" });
  const voteSubmitLockRef = useRef<boolean>(false);

  const needsTurnstile = authStatus !== "loading" && !isAuthed && anonVoteTurnstileRequired;
  const turnstileMisconfigured = needsTurnstile && !turnstileSiteKey;

  useEffect(() => {
    statusRef.current = status;
  }, [status]);

  useEffect(() => {
    let cancelled = false;

    async function loadPublicConfig() {
      try {
        const payload = (await apiGet("/public-config")) as Record<string, unknown>;
        if (cancelled) return;
        setAnonVoteTurnstileRequired(payload?.anon_vote_turnstile_required === true);
      } catch {
        if (cancelled) return;
        setAnonVoteTurnstileRequired(Boolean(turnstileSiteKey));
      }
    }

    void loadPublicConfig();
    return () => {
      cancelled = true;
    };
  }, [turnstileSiteKey]);

  const streamUrl = useMemo(() => {
    if (!resolvedBattleId) return null;
    return `${getBackendBaseUrl()}/battles/${encodeURIComponent(resolvedBattleId)}/stream`;
  }, [resolvedBattleId]);

  useEffect(() => {
    let cancelled = false;

    async function bootstrapBattle() {
      if (!isBattleBootstrapReady(authStatus)) return;

      setStatus("loading");
      setErrorText(null);
      setResolvedBattleId(null);
      setJpSource("");
      setJpSourceLang("JA");
      setTargetLang("ZH");
      setOutA("");
      setOutB("");
      replayPolicyRef.current = { A: "consume", B: "consume" };

      setWinner(null);
      setRubricTags([]);
      setComment("");
      setTurnstileToken("");
      setSubmittingVote(false);
      setReveal(null);
      voteSubmitLockRef.current = false;

      try {
        const battle = await loadOrCreateBattle<BattlePublic>(battleId, accessTokenRef.current);
        if (cancelled) return;

        setJpSource(battle.source_text);
        setJpSourceLang((battle.source_lang ?? "ja").toUpperCase());
        setTargetLang((battle.target_lang ?? "zh").toUpperCase());
        setOutA(battle.run_a?.output_text ?? "");
        setOutB(battle.run_b?.output_text ?? "");
        const isFinished = battle.status === "completed" || battle.status === "failed";
        replayPolicyRef.current = {
          A: isFinished && Boolean(battle.run_a?.output_text) ? "ignore" : "consume",
          B: isFinished && Boolean(battle.run_b?.output_text) ? "ignore" : "consume",
        };
        setStatus(battle.status === "completed" ? "done" : battle.status);
        setResolvedBattleId(battle.id);

        // If the battle was just created (battleId was "new" or differs
        // from the resolved id), update the URL so a page refresh doesn't
        // create another battle.
        if (battle.id !== battleId) {
          router.replace(`/battle/${encodeURIComponent(battle.id)}`, { scroll: false });
        }
      } catch (err) {
        if (cancelled) return;
        setStatus("error");
        setErrorText(err instanceof Error ? err.message : "Failed to load battle");
      }
    }

    void bootstrapBattle();

    return () => {
      cancelled = true;
    };
  }, [battleId, authStatus, restartKey, router]);

  useEffect(() => {
    if (!streamUrl || !resolvedBattleId) return;
    const url = streamUrl;
    const abortController = new AbortController();

    let cancelled = false;

    async function runStream() {
      const startedFromTerminalState =
        statusRef.current === "done" || statusRef.current === "failed";
      let sawTerminalEvent = false;

      try {
        setStatus((prev) => (prev === "done" || prev === "failed" ? prev : "streaming"));
        for await (const evt of streamSSE(url, {
          headers: accessTokenRef.current ? { Authorization: `Bearer ${accessTokenRef.current}` } : undefined,
          signal: abortController.signal,
        })) {
          if (cancelled) return;

          if (evt.event === "sse.retry") {
            // SSE layer is about to reconnect — reset text to prevent
            // duplication from partially-consumed previous stream.
            replayPolicyRef.current = { A: "consume", B: "consume" };
            setOutA("");
            setOutB("");
            setStatus((prev) => (prev === "done" || prev === "failed" ? prev : "reconnecting"));
            continue;
          }

          if (evt.event === "run.delta") {
            const payload = asRecord(evt.data);
            const side = payload?.side;
            const delta = payload?.text_delta;
            const replay = payload?.replay === true;
            const chunkIndexRaw = payload?.chunk_index;
            const chunkIndex = typeof chunkIndexRaw === "number" ? chunkIndexRaw : null;

            if (
              replay &&
              (side === "A" || side === "B") &&
              replayPolicyRef.current[side] === "ignore"
            ) {
              continue;
            }

            if (side === "A" && typeof delta === "string") {
              setOutA((prev) => mergeBattleDelta(prev, delta, replay, chunkIndex));
            }
            if (side === "B" && typeof delta === "string") {
              setOutB((prev) => mergeBattleDelta(prev, delta, replay, chunkIndex));
            }
          }

          if (evt.event === "run.error") {
            setStatus("error");
            const errorPayload = asRecord(evt.data);
            const errorDetail = typeof errorPayload?.error === "string" ? errorPayload.error : null;
            setErrorText((prev) => prev ?? (errorDetail ? `Run error: ${errorDetail}` : "A translation run encountered an error"));
          }
          if (evt.event === "battle.error") {
            sawTerminalEvent = true;
            const payload = asRecord(evt.data);
            const detail = typeof payload?.detail === "string" ? payload.detail : null;
            setStatus("error");
            setErrorText(detail ? `Battle error: ${detail}` : "Battle stream failed");
            break;
          }
          if (evt.event === "battle.completed") {
            sawTerminalEvent = true;
            setStatus("done");
            break;
          }
          if (evt.event === "battle.failed") {
            sawTerminalEvent = true;
            setStatus("failed");
            break;
          }
        }

        if (!cancelled && !sawTerminalEvent && !startedFromTerminalState) {
          setStatus((prev) => (prev === "done" || prev === "failed" ? prev : "error"));
          setErrorText((prev) => prev ?? "Battle stream ended before completion");
        }
      } catch (err) {
        if (cancelled) return;
        setStatus("error");
        setErrorText(err instanceof Error ? err.message : "Battle stream failed");
      }
    }

    void runStream();

    return () => {
      cancelled = true;
      abortController.abort();
    };
    // Note: accessToken is intentionally excluded from the dependency array.
    // The stream uses accessTokenRef to pick up token refreshes without
    // tearing down and reconnecting the SSE stream mid-flight.
  }, [streamUrl, resolvedBattleId]);

  async function handleVoteSubmit() {
    if (voteSubmitLockRef.current || !resolvedBattleId || !winner || reveal) return;

    voteSubmitLockRef.current = true;
    setSubmittingVote(true);
    setErrorText(null);
    try {
      const payload = {
        winner,
        rubric: { tags: rubricTags },
        comment: comment || null,
        turnstile_token: turnstileToken || null,
      };
      const result = (await apiPost(
        `/battles/${encodeURIComponent(resolvedBattleId)}/vote`,
        payload,
        {
          headers: accessTokenRef.current ? { Authorization: `Bearer ${accessTokenRef.current}` } : undefined,
        },
      )) as VoteSubmitResponse;
      setReveal(result.reveal);
    } catch (err) {
      setErrorText(err instanceof Error ? err.message : "Failed to submit vote");
      // Clear turnstile token so the widget can generate a fresh one on retry.
      setTurnstileToken("");
    } finally {
      voteSubmitLockRef.current = false;
      setSubmittingVote(false);
    }
  }

  function handleStartAnotherBattle() {
    const nonce = Date.now().toString(36);
    router.push(`/battle/new?r=${nonce}`);
  }

  function toggleRubricTag(tag: string) {
    setRubricTags((prev) =>
      prev.includes(tag) ? prev.filter((item) => item !== tag) : [...prev, tag],
    );
  }

  const canVote =
    resolvedBattleId !== null &&
    authStatus !== "loading" &&
    winner !== null &&
    reveal === null &&
    !submittingVote &&
    status === "done" &&
    (!needsTurnstile || Boolean(turnstileToken)) &&
    !turnstileMisconfigured;

  const statusLabel =
    status === "done" ? "Complete" :
    status === "streaming" ? "Translating..." :
    status === "reconnecting" ? "Reconnecting..." :
    status === "error" || status === "failed" ? "Error" :
    status === "loading" ? "Loading..." :
    status.charAt(0).toUpperCase() + status.slice(1);

  return (
    <motion.div
      initial={{ opacity: 0, y: 15 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5 }}
      className="grid gap-6"
    >
      {/* Source text panel */}
      <div className="glass-panel-accent p-6">
        <div className="flex items-center justify-between gap-3 mb-4">
          <div className="flex items-center gap-3">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg border border-primary/15 bg-primary/[0.08]">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4 text-primary/80" aria-hidden>
                <path d="M4 7V4h16v3" />
                <path d="M9 20h6" />
                <path d="M12 4v16" />
              </svg>
            </div>
            <div className="flex items-center gap-2.5">
              <h2 className="text-xl font-bold heading-gradient">Source Text</h2>
              <span className="lang-badge-jp">{jpSourceLang}</span>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground mr-2">
            <span className={`inline-block h-2 w-2 rounded-full ${
              status === 'done' ? 'bg-emerald-500' :
              status === 'error' || status === 'failed' ? 'bg-red-500' :
              'bg-primary animate-pulse'
            }`} />
            <span className="text-xs font-medium">{statusLabel}</span>
            {resolvedBattleId && <span className="opacity-30 text-xs font-mono">| {resolvedBattleId.substring(0, 8)}</span>}
          </div>
        </div>
        <pre className="m-0 whitespace-pre-wrap text-foreground leading-relaxed text-lg font-inherit">
          {jpSource || <span className="text-muted-foreground shimmer inline-block w-full h-6 rounded" />}
        </pre>
      </div>

      {/* Translation panels */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <Panel side="A" title="Model A" text={outA} reveal={reveal?.A} delay={0.1} isStreaming={status === "streaming"} langBadge={targetLang} />
        <Panel side="B" title="Model B" text={outB} reveal={reveal?.B} delay={0.2} isStreaming={status === "streaming"} langBadge={targetLang} />
      </div>

      {/* Voting section */}
      <motion.div
        initial={{ opacity: 0, y: 15 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5, delay: 0.3 }}
        className="glass-panel-accent p-8 flex flex-col items-center gap-6"
      >
        <div className="w-full max-w-2xl text-center">
          <h3 className="mb-2 text-xl font-bold heading-gradient">Cast Your Vote</h3>
          <p className="mb-5 text-sm text-muted-foreground">Which translation is better?</p>
          <div className="flex flex-wrap justify-center gap-3">
            <VoteOption label="Model A is better" side="A" active={winner === "A"} onClick={() => setWinner("A")} />
            <VoteOption label="Tie" active={winner === "tie"} onClick={() => setWinner("tie")} />
            <VoteOption label="Model B is better" side="B" active={winner === "B"} onClick={() => setWinner("B")} />
          </div>
        </div>

        <div className="w-full max-w-2xl">
          <div className="divider-fade" />
        </div>

        <div className="w-full max-w-2xl">
          <div className="mb-3 text-sm font-medium text-muted-foreground">Why did you choose this? (optional tags)</div>
          <div className="flex flex-wrap gap-2">
            {RUBRIC_TAGS.map((tag) => (
              <button
                key={tag}
                type="button"
                onClick={() => toggleRubricTag(tag)}
                aria-pressed={rubricTags.includes(tag)}
                className={`
                  rounded-full border transition-all duration-200 ease-out outline-none
                  px-3.5 py-1.5 text-xs capitalize font-medium
                  ${rubricTags.includes(tag)
                    ? "border-primary/40 bg-primary/15 text-primary font-semibold shadow-sm shadow-primary/10"
                    : "border-border bg-transparent text-muted-foreground hover:border-foreground/20 hover:bg-foreground/5 hover:text-foreground"
                  }
                `}
              >
                {RUBRIC_ICONS[tag] ?? tag}
              </button>
            ))}
          </div>
        </div>

        <div className="w-full max-w-2xl">
          <label className="mb-2 block text-sm font-medium text-muted-foreground" htmlFor="vote-comment">
            Optional feedback
          </label>
          <textarea
            id="vote-comment"
            value={comment}
            onChange={(evt) => setComment(evt.target.value)}
            rows={3}
            placeholder="What influenced your decision?"
            className="textarea-premium"
          />
        </div>

        <div className="w-full max-w-2xl">
          {!isAuthed ? (
            <div className="mb-4">
              {needsTurnstile && turnstileSiteKey ? (
                <div className="flex justify-center">
                  <TurnstileWidget
                    siteKey={turnstileSiteKey}
                    onToken={(token) => setTurnstileToken(token)}
                    onExpire={() => setTurnstileToken("")}
                    onError={(message) => {
                      setTurnstileToken("");
                      setErrorText(message);
                    }}
                  />
                </div>
              ) : needsTurnstile ? (
                <div className="text-center text-sm text-destructive">
                  Backend requires Turnstile for anonymous voting, but <code>NEXT_PUBLIC_TURNSTILE_SITE_KEY</code> is missing.
                </div>
              ) : null}
            </div>
          ) : null}

          <div className="flex flex-col items-center justify-center gap-4">
            <Button
              type="button"
              onClick={() => void handleVoteSubmit()}
              disabled={!canVote}
              size="lg"
              className="h-12 w-full max-w-md rounded-full text-lg shadow-lg shadow-primary/20 hover:shadow-primary/30 hover:scale-[1.01] transition-all duration-200"
            >
              {submittingVote ? "Submitting..." : "Submit Vote"}
            </Button>

            {(reveal || status === "failed" || status === "error") && (
              <Button
                type="button"
                variant="ghost"
                onClick={handleStartAnotherBattle}
                className="rounded-full text-muted-foreground hover:text-foreground"
              >
                Start another battle
              </Button>
            )}
          </div>

          {errorText ? <p className="mt-4 text-center text-sm text-destructive">{errorText}</p> : null}
        </div>
      </motion.div>

      {/* Reveal section */}
      <AnimatePresence>
        {reveal && (
          <motion.div
            initial={{ opacity: 0, y: 20, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            transition={{ duration: 0.5 }}
            className="glass-panel-accent p-8 text-center celebrate relative overflow-hidden"
          >
            {/* Decorative sparkle dots */}
            {[
              { top: "10%", left: "15%", "--tx": "-20px", "--ty": "-30px", animationDelay: "0s" },
              { top: "20%", right: "18%", "--tx": "25px", "--ty": "-25px", animationDelay: "0.1s" },
              { top: "15%", left: "40%", "--tx": "10px", "--ty": "-35px", animationDelay: "0.15s" },
              { top: "12%", right: "35%", "--tx": "-15px", "--ty": "-20px", animationDelay: "0.2s" },
              { top: "25%", left: "25%", "--tx": "-30px", "--ty": "-10px", animationDelay: "0.05s" },
              { top: "22%", right: "22%", "--tx": "20px", "--ty": "-15px", animationDelay: "0.25s" },
              { top: "18%", left: "55%", "--tx": "15px", "--ty": "-40px", animationDelay: "0.08s" },
              { top: "28%", right: "30%", "--tx": "-10px", "--ty": "-25px", animationDelay: "0.18s" },
            ].map((style, i) => (
              <span
                key={i}
                className="sparkle-dot"
                style={style as React.CSSProperties}
                aria-hidden
              />
            ))}

            {/* Winner glow */}
            <div className="absolute inset-0 bg-gradient-to-b from-emerald-500/[0.03] to-transparent pointer-events-none" />

            <motion.div
              initial={{ scale: 0, rotate: -90 }}
              animate={{ scale: 1, rotate: 0 }}
              transition={{ duration: 0.4, delay: 0.1, type: "spring", stiffness: 200 }}
              className="mb-4 mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-emerald-500/10 border border-emerald-500/20"
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5 text-emerald-600 dark:text-emerald-400" aria-hidden>
                <polyline points="20 6 9 17 4 12" />
              </svg>
            </motion.div>
            <h3 className="text-lg font-bold heading-sakura mb-2">Models Revealed</h3>
            <p className="text-xs text-muted-foreground/60 mb-5">Thank you for your vote! Here are the models behind each translation.</p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 max-w-lg mx-auto">
              <motion.div
                initial={{ opacity: 0, x: -20 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ duration: 0.4, delay: 0.2 }}
                className={`rounded-xl border p-4 relative overflow-hidden ${
                  winner === "A"
                    ? "border-blue-500/25 bg-blue-500/[0.04]"
                    : "border-border/50 bg-foreground/[0.02]"
                }`}
              >
                {winner === "A" && (
                  <div className="absolute top-2 right-2">
                    <span className="badge border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 text-[10px]">Winner</span>
                  </div>
                )}
                <div className="flex items-center gap-2 mb-1.5">
                  <span className="flex h-5 w-5 items-center justify-center rounded text-[10px] font-bold bg-blue-500/10 text-blue-600 dark:text-blue-400 border border-blue-500/20">A</span>
                  <span className="text-xs text-muted-foreground">Model A</span>
                </div>
                <div className="font-semibold text-foreground">{reveal.A.display_name}</div>
              </motion.div>
              <motion.div
                initial={{ opacity: 0, x: 20 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ duration: 0.4, delay: 0.3 }}
                className={`rounded-xl border p-4 relative overflow-hidden ${
                  winner === "B"
                    ? "border-amber-500/25 bg-amber-500/[0.04]"
                    : "border-border/50 bg-foreground/[0.02]"
                }`}
              >
                {winner === "B" && (
                  <div className="absolute top-2 right-2">
                    <span className="badge border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 text-[10px]">Winner</span>
                  </div>
                )}
                <div className="flex items-center gap-2 mb-1.5">
                  <span className="flex h-5 w-5 items-center justify-center rounded text-[10px] font-bold bg-amber-500/10 text-amber-600 dark:text-amber-400 border border-amber-500/20">B</span>
                  <span className="text-xs text-muted-foreground">Model B</span>
                </div>
                <div className="font-semibold text-foreground">{reveal.B.display_name}</div>
              </motion.div>
            </div>
            {winner === "tie" && (
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ delay: 0.4 }}
                className="mt-4 text-xs text-muted-foreground/60"
              >
                You voted this as a tie
              </motion.div>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}

function VoteOption({
  label,
  side,
  active,
  onClick,
}: {
  label: string;
  side?: "A" | "B";
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`
        rounded-full border transition-all duration-200 ease-out outline-none
        px-5 py-2.5 text-sm font-medium
        ${active
          ? "border-primary/40 bg-primary/15 text-primary font-semibold shadow-md shadow-primary/10 scale-[1.02]"
          : "border-border bg-transparent text-foreground hover:border-foreground/20 hover:bg-foreground/5"
        }
      `}
    >
      {side === "A" && <span className="mr-1 opacity-70">{"<"}</span>}
      {label}
      {side === "B" && <span className="ml-1 opacity-70">{">"}</span>}
    </button>
  );
}

function Panel({
  side,
  title,
  text,
  reveal,
  delay = 0,
  isStreaming = false,
  langBadge = "ZH",
}: {
  side: "A" | "B";
  title: string;
  text: string;
  reveal?: { model_id: string; display_name: string };
  delay?: number;
  isStreaming?: boolean;
  langBadge?: string;
}) {
  const isActivelyStreaming = isStreaming && Boolean(text);

  return (
    <motion.section
      initial={{ opacity: 0, scale: 0.95 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ duration: 0.5, delay }}
      className="flex h-full flex-col glass-panel relative overflow-hidden"
    >
      {/* Side color accent */}
      <div className={`absolute inset-y-0 ${side === "A" ? "left-0" : "right-0"} w-px bg-gradient-to-b from-transparent ${side === "A" ? "via-blue-400/20" : "via-amber-400/20"} to-transparent`} />

      {/* Top accent line with side color */}
      <div className={`absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent ${side === "A" ? "via-blue-400/15" : "via-amber-400/15"} to-transparent`} />

      <div className="mb-0 flex items-center justify-between border-b border-foreground/[0.06] p-5">
        <div className="flex items-center gap-2.5">
          <span className={`flex h-6 w-6 items-center justify-center rounded-md text-xs font-bold ${
            side === "A"
              ? "bg-blue-500/10 text-blue-600 dark:text-blue-400 border border-blue-500/20"
              : "bg-amber-500/10 text-amber-600 dark:text-amber-400 border border-amber-500/20"
          }`}>
            {side}
          </span>
          <h3 className="text-base font-semibold text-muted-foreground">{title}</h3>
          <span className="lang-badge-zh">{langBadge}</span>
        </div>
        <div className="flex items-center gap-2">
          {isActivelyStreaming && (
            <span className="flex items-center gap-1.5 text-xs text-muted-foreground/50">
              <span className="inline-block h-1.5 w-1.5 rounded-full bg-primary animate-pulse" />
              Streaming
            </span>
          )}
          {isStreaming && !text && (
            <span className="text-xs text-muted-foreground/50 animate-pulse">Translating...</span>
          )}
          {reveal && (
            <motion.span
              initial={{ opacity: 0, scale: 0.9 }}
              animate={{ opacity: 1, scale: 1 }}
              className="badge-sakura"
            >
              {reveal.display_name}
            </motion.span>
          )}
        </div>
      </div>
      <div className="flex-grow p-5">
        <pre className={`m-0 whitespace-pre-wrap font-inherit text-lg leading-relaxed text-foreground ${isActivelyStreaming ? "typing-cursor" : ""}`}>
          {text || (
            <span className="text-muted-foreground/50 italic text-base flex items-center gap-2">
              {isStreaming ? (
                <>
                  <span className="inline-block h-1.5 w-1.5 rounded-full bg-primary animate-pulse" />
                  Waiting for output...
                </>
              ) : (
                "Waiting for output..."
              )}
            </span>
          )}
        </pre>
      </div>
    </motion.section>
  );
}
