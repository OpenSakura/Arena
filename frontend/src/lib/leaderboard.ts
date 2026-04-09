/**
 * frontend/src/lib/leaderboard.ts
 *
 * Pure leaderboard query/presentation helpers.
 */

export type LeaderboardMethod = "elo" | "bt";

export type LeaderboardSearchParams = {
  method?: string;
  include_confidence?: string;
};

export type ConfidenceRow = {
  rating_lower: number | null;
  rating_upper: number | null;
};

export function isEnabled(value: string | undefined): boolean {
  if (!value) return false;
  return value === "1" || value.toLowerCase() === "true";
}

export function buildLeaderboardQuery(searchParams?: LeaderboardSearchParams): {
  selectedMethod: LeaderboardMethod;
  includeConfidence: boolean;
  query: string;
} {
  const selectedMethod: LeaderboardMethod = searchParams?.method === "bt" ? "bt" : "elo";
  const includeConfidence = isEnabled(searchParams?.include_confidence);
  const query = `/leaderboard?method=${selectedMethod}${includeConfidence ? "&include_confidence=true" : ""}`;

  return { selectedMethod, includeConfidence, query };
}

export function hasConfidenceIntervals(rows: ConfidenceRow[]): boolean {
  return rows.some((row) => row.rating_lower !== null && row.rating_upper !== null);
}
