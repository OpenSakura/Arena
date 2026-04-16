/**
 * frontend/src/routes/LeaderboardRoute.tsx
 *
 * SPA Route for the leaderboard using client-side data fetching.
 */

import { useState, useEffect } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { apiGet } from "@/lib/api";
import {
  buildLeaderboardQuery,
  hasConfidenceIntervals,
} from "@/lib/leaderboard";
import { isRecord } from "@/lib/typeGuards";

type LeaderboardRow = {
  model_id: string;
  display_name: string;
  rating: number;
  rating_lower: number | null;
  rating_upper: number | null;
  games_played: number;
};

type LeaderboardResponse = {
  method: "elo" | "bt";
  ci: boolean;
  bootstrap_rounds: number | null;
  models: LeaderboardRow[];
};

const LEADERBOARD_METHODS: ReadonlyArray<LeaderboardResponse["method"]> = ["elo", "bt"];

function isLeaderboardRow(value: unknown): value is LeaderboardRow {
  return (
    isRecord(value) &&
    typeof value.model_id === "string" &&
    typeof value.display_name === "string" &&
    typeof value.rating === "number" &&
    (typeof value.rating_lower === "number" || value.rating_lower === null) &&
    (typeof value.rating_upper === "number" || value.rating_upper === null) &&
    typeof value.games_played === "number"
  );
}

function isLeaderboardResponse(value: unknown): value is LeaderboardResponse {
  return (
    isRecord(value) &&
    typeof value.method === "string" &&
    LEADERBOARD_METHODS.includes(value.method as LeaderboardResponse["method"]) &&
    typeof value.ci === "boolean" &&
    (typeof value.bootstrap_rounds === "number" || value.bootstrap_rounds === null) &&
    Array.isArray(value.models) &&
    value.models.every(isLeaderboardRow)
  );
}

function parseLeaderboardResponse(value: unknown): LeaderboardResponse {
  if (!isLeaderboardResponse(value)) {
    throw new Error("Invalid leaderboard response");
  }

  return value;
}

function rankBadge(index: number) {
  if (index === 0) return "bg-amber-500/15 text-amber-600 dark:text-amber-400 border-amber-500/25 shadow-sm shadow-amber-500/10";
  if (index === 1) return "bg-zinc-400/15 text-zinc-700 dark:text-zinc-300 border-zinc-400/25";
  if (index === 2) return "bg-orange-500/15 text-orange-600 dark:text-orange-400 border-orange-500/25";
  return "bg-foreground/5 text-muted-foreground border-transparent";
}

function RankIcon({ index }: { index: number }) {
  if (index === 0) {
    return (
      <svg viewBox="0 0 24 24" className="h-3.5 w-3.5 text-amber-600 dark:text-amber-400" fill="currentColor" aria-hidden>
        <path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z" />
      </svg>
    );
  }
  return null;
}

function MedalIcon({ index }: { index: number }) {
  const colors = [
    { fill: "text-amber-600 dark:text-amber-400", bg: "bg-amber-500/10", border: "border-amber-500/20" },
    { fill: "text-zinc-700 dark:text-zinc-300", bg: "bg-zinc-400/10", border: "border-zinc-400/20" },
    { fill: "text-orange-600 dark:text-orange-400", bg: "bg-orange-500/10", border: "border-orange-500/20" },
  ];
  const c = colors[index];
  if (!c) return null;
  return (
    <div className={`flex h-10 w-10 items-center justify-center rounded-full ${c.bg} border ${c.border}`}>
      <svg viewBox="0 0 24 24" className={`h-5 w-5 ${c.fill}`} fill="currentColor" aria-hidden>
        <path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z" />
      </svg>
    </div>
  );
}

function podiumClass(index: number) {
  if (index === 0) return "podium-gold";
  if (index === 1) return "podium-silver";
  if (index === 2) return "podium-bronze";
  return "";
}

export default function LeaderboardRoute() {
  const [searchParams] = useSearchParams();
  
  const searchParamsObj: Record<string, string> = {};
  searchParams.forEach((value, key) => {
    searchParamsObj[key] = value;
  });

  const request = buildLeaderboardQuery(searchParamsObj);
  
  const [data, setData] = useState<{
    models: LeaderboardRow[];
    selectedMethod: "elo" | "bt";
    includeConfidence: boolean;
    bootstrapRounds: number | null;
  }>({
    models: [],
    selectedMethod: request.selectedMethod,
    includeConfidence: request.includeConfidence,
    bootstrapRounds: null,
  });
  
  const [errorText, setErrorText] = useState<string | null>(null);

  useEffect(() => {
    let ignore = false;
    
    // We optimistically set method and CI from the URL params while loading
    setData(prev => ({
      ...prev,
      selectedMethod: request.selectedMethod,
      includeConfidence: request.includeConfidence,
    }));
    setErrorText(null);

    apiGet(request.query)
      .then((res) => {
        if (!ignore) {
          const parsed = parseLeaderboardResponse(res);
          setData({
            models: parsed.models,
            selectedMethod: parsed.method,
            includeConfidence: parsed.ci,
            bootstrapRounds: parsed.bootstrap_rounds,
          });
        }
      })
      .catch((err) => {
        if (!ignore) {
          setErrorText(err instanceof Error ? err.message : "Failed to load leaderboard");
        }
      });
      
    return () => {
      ignore = true;
    };
  }, [request.query, request.selectedMethod, request.includeConfidence]);

  const { models, selectedMethod, includeConfidence, bootstrapRounds } = data;

  const hasConfidence = hasConfidenceIntervals(models);
  const confidenceToggleHref = includeConfidence
    ? `/leaderboard?method=${selectedMethod}`
    : `/leaderboard?method=${selectedMethod}&include_confidence=true`;
  const confidenceToggleLabel = includeConfidence ? "Hide 95% CI" : "Show 95% CI";

  const maxRating = models.length > 0 ? Math.max(...models.map((m) => m.rating ?? 0)) : 0;

  return (
    <div className="grid gap-6">
      {/* Header */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl border border-primary/15 bg-primary/[0.08]">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="h-4.5 w-4.5 text-primary/80" aria-hidden>
              <line x1="18" y1="20" x2="18" y2="10" />
              <line x1="12" y1="20" x2="12" y2="4" />
              <line x1="6" y1="20" x2="6" y2="14" />
            </svg>
          </div>
          <div>
            <h2 className="heading-gradient text-3xl">Leaderboard</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Method: <span className="font-semibold text-foreground/80">{selectedMethod.toUpperCase()}</span>
              {includeConfidence && bootstrapRounds
                ? ` (${bootstrapRounds} bootstrap rounds)`
                : ""}
            </p>
          </div>
        </div>

        {/* Method toggles */}
        <div className="flex items-center gap-1.5 rounded-xl border border-border/50 bg-background/30 p-1 backdrop-blur">
          <Link
            to={`/leaderboard?method=elo${includeConfidence ? "&include_confidence=true" : ""}`}
            aria-label="Elo (baseline)"
            className={`rounded-lg px-4 py-1.5 text-xs font-semibold transition-all ${
              selectedMethod === "elo"
                ? "bg-primary/10 text-primary shadow-sm"
                : "text-muted-foreground hover:bg-foreground/5 hover:text-foreground"
            }`}
          >
            Elo
          </Link>
          <Link
            to={`/leaderboard?method=bt${includeConfidence ? "&include_confidence=true" : ""}`}
            className={`rounded-lg px-4 py-1.5 text-xs font-semibold transition-all ${
              selectedMethod === "bt"
                ? "bg-primary/10 text-primary shadow-sm"
                : "text-muted-foreground hover:bg-foreground/5 hover:text-foreground"
            }`}
          >
            Bradley-Terry
          </Link>
          <div className="h-4 w-px bg-border/50 mx-0.5" />
          <Link
            to={confidenceToggleHref}
            aria-label={confidenceToggleLabel}
            className={`rounded-lg px-3 py-1.5 text-xs font-semibold transition-all ${
              includeConfidence
                ? "bg-primary/10 text-primary"
                : "text-muted-foreground hover:bg-foreground/5 hover:text-foreground"
            }`}
          >
            95% CI
          </Link>
        </div>
      </div>

      {errorText ? (
        <div className="glass-panel p-6">
          <p className="text-sm text-destructive">{errorText}</p>
        </div>
      ) : null}

      {!errorText && models.length === 0 ? (
        <div className="empty-state animate-fade-in-up">
          <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-2xl border border-primary/15 bg-primary/[0.06]">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="h-6 w-6 text-primary/50" aria-hidden>
              <line x1="18" y1="20" x2="18" y2="10" />
              <line x1="12" y1="20" x2="12" y2="4" />
              <line x1="6" y1="20" x2="6" y2="14" />
            </svg>
          </div>
          <div className="text-lg font-semibold text-foreground/60 mb-2">No ratings yet</div>
          <p className="text-muted-foreground text-sm max-w-xs">
            Start a battle and cast some votes to see models ranked here.
          </p>
          <Link
            to="/battle/new"
            className="mt-5 inline-block rounded-full border border-primary/20 bg-primary/10 px-6 py-2 text-sm font-semibold text-primary transition-all hover:bg-primary/20 hover:scale-[1.02]"
          >
            Start a battle
          </Link>
        </div>
      ) : null}

      {/* Table */}
      {models.length > 0 ? (
        <>
          {/* Podium cards for top 3 */}
          {models.length >= 2 && (
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              {models.slice(0, Math.min(3, models.length)).map((row, index) => (
                <div
                  key={row.model_id}
                  className={`glass-panel border p-5 text-center relative overflow-hidden hover-lift ${podiumClass(index)}`}
                >
                  {/* Top accent */}
                  <div className={`absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent ${
                    index === 0 ? "via-amber-400/25" : index === 1 ? "via-zinc-400/20" : "via-orange-400/20"
                  } to-transparent`} />

                  <div className="flex flex-col items-center gap-3">
                    <MedalIcon index={index} />
                    <div>
                      <div className="text-sm font-bold text-foreground mb-0.5">{row.display_name}</div>
                      <div className="text-xs text-muted-foreground">
                        #{index + 1} Rank
                      </div>
                    </div>
                    <div className="flex items-baseline gap-1">
                      {row.games_played === 0 ? (
                        <span className="text-sm font-medium text-muted-foreground/60 uppercase tracking-wider">Unrated</span>
                      ) : (
                        <>
                          <span className="text-2xl font-bold tabular-nums font-mono text-foreground">{(row.rating ?? 0).toFixed(0)}</span>
                          <span className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground/60">rating</span>
                        </>
                      )}
                    </div>
                    {hasConfidence && row.rating_lower !== null && row.rating_upper !== null && (
                      <div className="text-[11px] text-muted-foreground/50 tabular-nums font-mono">
                        {row.rating_lower.toFixed(1)} &ndash; {row.rating_upper.toFixed(1)}
                      </div>
                    )}
                    <div className="text-[10px] text-muted-foreground/40">
                      {row.games_played === 0 ? (
                        <span className="inline-block rounded-full border border-primary/15 bg-primary/[0.05] px-1.5 py-px text-[9px] font-semibold uppercase tracking-wider text-primary/60">New</span>
                      ) : (
                        `${row.games_played} games`
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}

          <div className="glass-panel overflow-hidden">
            <div className="overflow-x-auto">
          <table className="w-full border-collapse min-w-[480px]">
            <thead>
              <tr className="bg-foreground/[0.02]">
                <th className="th-premium w-20">Rank</th>
                <th className="th-premium">Model</th>
                <th className="th-premium text-right">Rating</th>
                {hasConfidence ? (
                  <th className="th-premium text-right">95% CI</th>
                ) : null}
                <th className="th-premium text-right">Games</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/30">
              {models.map((row, index) => (
                <tr
                  key={row.model_id}
                  className="transition-colors hover:bg-foreground/[0.02] group"
                >
                  <td className="td-premium">
                    <div className="flex items-center gap-1.5">
                      <span
                        className={`inline-flex h-7 w-7 items-center justify-center rounded-full border text-xs font-bold ${rankBadge(index)}`}
                      >
                        {index + 1}
                      </span>
                      <RankIcon index={index} />
                    </div>
                  </td>
                  <td className="td-premium">
                    <div className="font-semibold group-hover:text-primary transition-colors">{row.display_name}</div>
                    {/* Rating bar */}
                    <div className="mt-1.5 h-1.5 w-full max-w-[200px] rounded-full bg-foreground/5 overflow-hidden">
                      <div
                        className={`h-full rounded-full transition-all duration-500 ${
                          row.games_played === 0
                            ? "bg-foreground/[0.06]"
                            : index === 0
                              ? "bg-gradient-to-r from-amber-500/50 to-amber-400/30"
                              : "bg-gradient-to-r from-primary/40 to-primary/20"
                        }`}
                        style={{ width: row.games_played === 0 ? "100%" : `${maxRating > 0 ? (row.rating / maxRating) * 100 : 0}%` }}
                      />
                    </div>
                  </td>
                  <td className="td-premium text-right tabular-nums font-mono text-sm font-semibold">
                    {row.games_played === 0 ? (
                      <span className="text-xs font-medium text-muted-foreground/50 uppercase tracking-wider not-italic">Unrated</span>
                    ) : (
                      (row.rating ?? 0).toFixed(1)
                    )}
                  </td>
                  {hasConfidence ? (
                    <td className="td-premium text-right tabular-nums font-mono text-xs text-muted-foreground">
                      {row.rating_lower !== null && row.rating_upper !== null
                        ? `${row.rating_lower.toFixed(1)} \u2013 ${row.rating_upper.toFixed(1)}`
                        : "\u2014"}
                    </td>
                  ) : null}
                  <td className="td-premium text-right tabular-nums text-muted-foreground">
                    {row.games_played === 0 ? (
                      <span className="inline-block rounded-full border border-primary/20 bg-primary/[0.06] px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-primary/70">New</span>
                    ) : (
                      row.games_played
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        </div>
        </>
      ) : null}
    </div>
  );
}
