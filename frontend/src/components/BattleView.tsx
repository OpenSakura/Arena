/**
 * frontend/src/components/BattleView.tsx
 *
 * Battle UI component with Arena Styling.
 * State management is delegated to useBattle hook.
 */

import { motion, AnimatePresence } from "framer-motion";
import { Button } from "@/components/ui/button";
import { SESSION_EXPIRED_MESSAGE } from "@/auth/session";

import { useBattle } from "@/hooks/useBattle";

const RUBRIC_TAGS = [
  "accuracy",
  "fluency",
  "style",
  "consistency",
  "naturalness",
] as const;

function formatRubricTag(tag: string) {
  return tag.charAt(0).toUpperCase() + tag.slice(1);
}

export function BattleView({ battleId }: { battleId: string }) {
  const {
    state,
    dispatch,
    isAuthed,
    hasSessionError,
    canVote,
    canRetry,
    voteSubmitted,
    statusLabel,
    handleVoteSubmit,
    handleRetry,
    handleStartAnotherBattle,
  } = useBattle(battleId);

  const {
    resolvedBattleId,
    jpSource,
    jpSourceLang,
    targetLang,
    outA,
    outB,
    status,
    errorText,
    winner,
    rubricTags,
    comment,
    submittingVote,
    reveal,
  } = state;



  function toggleRubricTag(tag: string) {
    dispatch({ type: "TOGGLE_RUBRIC_TAG", tag });
  }

  if (status === "error" && !resolvedBattleId) {
    return (
      <div className="flex h-[50vh] flex-col items-center justify-center gap-4 text-center">
        <div className="flex h-12 w-12 items-center justify-center rounded-full bg-destructive/10 text-destructive">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-6 w-6">
            <circle cx="12" cy="12" r="10" />
            <line x1="12" y1="8" x2="12" y2="12" />
            <line x1="12" y1="16" x2="12.01" y2="16" />
          </svg>
        </div>
        <h2 className="text-xl font-semibold">Unable to load battle</h2>
        <p className="text-muted-foreground max-w-md">{errorText}</p>
        {!isAuthed && (
          <Button onClick={() => window.location.href = "/"} variant="outline" className="mt-4">
            Return Home
          </Button>
        )}
      </div>
    );
  }

  return (
    <div
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
        <Panel side="A" title="Model A" text={outA} reveal={reveal?.A} isStreaming={status === "streaming"} langBadge={targetLang} />
        <Panel side="B" title="Model B" text={outB} reveal={reveal?.B} isStreaming={status === "streaming"} langBadge={targetLang} />
      </div>

      {/* Voting section */}
      {(status === "done" || status === "failed" || status === "error") && (
        <div
          className="glass-panel-accent p-8 flex flex-col items-center gap-6"
        >
          {status === "done" && (
            hasSessionError ? (
              <div className="w-full max-w-2xl text-center glass-panel-accent p-6 border-red-500/20 bg-red-500/5">
                <div className="flex flex-col items-center gap-3">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="h-8 w-8 text-red-600/80" aria-hidden>
                    <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
                  </svg>
                  <h3 className="text-lg font-semibold text-foreground">Session Expired</h3>
                  <p className="text-sm text-muted-foreground max-w-md mx-auto">
                    {SESSION_EXPIRED_MESSAGE}
                  </p>
                </div>
              </div>
            ) : (
            <>
              <div className="w-full max-w-2xl text-center">
                <h3 className="mb-2 text-xl font-bold heading-gradient">Cast Your Vote</h3>
                <p className="mb-5 text-sm text-muted-foreground">
                  {voteSubmitted && !reveal
                    ? "Vote recorded."
                    : "Which translation is better?"}
                </p>
                <div className="flex flex-wrap justify-center gap-3">
                  <VoteOption label="Model A is better" side="A" active={winner === "A"} disabled={reveal !== null} onClick={() => dispatch({ type: "SET_WINNER", winner: "A" })} />
                  <VoteOption label="Tie" active={winner === "tie"} disabled={reveal !== null} onClick={() => dispatch({ type: "SET_WINNER", winner: "tie" })} />
                  <VoteOption label="Model B is better" side="B" active={winner === "B"} disabled={reveal !== null} onClick={() => dispatch({ type: "SET_WINNER", winner: "B" })} />
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
                      disabled={reveal !== null}
                      aria-pressed={rubricTags.includes(tag)}
                      className={`
                        rounded-full border transition-all duration-200 ease-out outline-none
                        px-3.5 py-1.5 text-xs capitalize font-medium
                        ${reveal !== null ? "opacity-50 cursor-not-allowed" : ""}
                        ${rubricTags.includes(tag)
                          ? "border-primary/40 bg-primary/15 text-primary font-semibold shadow-sm shadow-primary/10"
                          : "border-border bg-transparent text-muted-foreground hover:border-foreground/20 hover:bg-foreground/5 hover:text-foreground"
                        }
                      `}
                    >
                      {formatRubricTag(tag)}
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
                  onChange={(evt) => dispatch({ type: "SET_COMMENT", comment: evt.target.value })}
                  rows={3}
                  disabled={reveal !== null}
                  placeholder="What influenced your decision?"
                  className={`textarea-premium ${reveal !== null ? "opacity-50 cursor-not-allowed" : ""}`}
                />
              </div>
            </>
            )
          )}

          <div className="w-full max-w-2xl">
            <div className="flex flex-col items-center justify-center gap-4">
              {/* Submit / Update Vote button */}
              {!reveal && status === "done" && (
                <>
                  <Button
                    type="button"
                    onClick={() => void handleVoteSubmit()}
                    disabled={!canVote}
                    size="lg"
                    className="h-12 w-full max-w-md rounded-full text-lg shadow-lg shadow-primary/20 hover:shadow-primary/30 hover:scale-[1.01] transition-all duration-200"
                  >
                    {submittingVote
                      ? "Submitting..."
                      : voteSubmitted
                        ? "Update Vote"
                        : "Submit Vote"}
                  </Button>
                </>
              )}

              {(reveal || status === "failed" || status === "error") && (
                <div className="flex flex-wrap justify-center gap-3">
                  {canRetry && (
                    <Button
                      type="button"
                      onClick={() => void handleRetry()}
                      variant="outline"
                      className="rounded-full text-muted-foreground hover:text-foreground hover:border-foreground/20"
                    >
                      Retry Battle
                    </Button>
                  )}
                  <Button
                    type="button"
                    variant="ghost"
                    onClick={handleStartAnotherBattle}
                    className="rounded-full text-muted-foreground hover:text-foreground"
                  >
                    Start another battle
                  </Button>
                </div>
              )}
            </div>

            {errorText ? <p className="mt-4 text-center text-sm text-destructive">{errorText}</p> : null}
          </div>
        </div>
      )}

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
    </div>
  );
}

function VoteOption({
  label,
  side,
  active,
  disabled = false,
  onClick,
}: {
  label: string;
  side?: "A" | "B";
  active: boolean;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-pressed={active}
      className={`
        rounded-full border transition-all duration-200 ease-out outline-none
        px-5 py-2.5 text-sm font-medium
        ${disabled ? "opacity-50 cursor-not-allowed" : ""}
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
  isStreaming = false,
  langBadge = "ZH",
}: {
  side: "A" | "B";
  title: string;
  text: string;
  reveal?: { model_id: string; display_name: string };
  isStreaming?: boolean;
  langBadge?: string;
}) {
  const isActivelyStreaming = isStreaming && Boolean(text);

  return (
    <section
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
    </section>
  );
}
