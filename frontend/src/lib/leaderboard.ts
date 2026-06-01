/**
 * frontend/src/lib/leaderboard.ts
 *
 * Pure leaderboard query/presentation helpers.
 */

export type LeaderboardMethod = "elo" | "bt";

export type JudgeType = "all" | "human" | "bot";

export type LeaderboardSearchParams = {
  method?: string;
  include_confidence?: string;
  judge_type?: string;
  exclude_refusals?: string;
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
  judgeType: JudgeType;
  excludeRefusals: boolean;
  query: string;
} {
  const selectedMethod: LeaderboardMethod = searchParams?.method === "bt" ? "bt" : "elo";
  const includeConfidence = isEnabled(searchParams?.include_confidence);
  const excludeRefusals = isEnabled(searchParams?.exclude_refusals);

  let judgeType: JudgeType = "all";
  if (searchParams?.judge_type === "human" || searchParams?.judge_type === "bot") {
    judgeType = searchParams.judge_type;
  }

  const queryParams = new URLSearchParams();
  queryParams.set("method", selectedMethod);
  if (includeConfidence) {
    queryParams.set("include_confidence", "true");
  }
  queryParams.set("judge_type", judgeType);
  if (excludeRefusals) {
    queryParams.set("exclude_refusals", "true");
  }

  const query = `/leaderboard?${queryParams.toString()}`;

  return { selectedMethod, includeConfidence, judgeType, excludeRefusals, query };
}

export function hasConfidenceIntervals(rows: ConfidenceRow[]): boolean {
  return rows.some((row) => row.rating_lower !== null && row.rating_upper !== null);
}
