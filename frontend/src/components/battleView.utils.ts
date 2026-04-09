import { apiGet, apiPost } from "../lib/api";

export async function loadOrCreateBattle<TBattle>(
  battleId: string,
  accessToken?: string,
): Promise<TBattle> {
  const headers = accessToken ? { Authorization: `Bearer ${accessToken}` } : undefined;

  if (battleId === "new") {
    return (await apiPost("/battles", {}, { headers })) as TBattle;
  }

  return (await apiGet(`/battles/${encodeURIComponent(battleId)}`, { headers })) as TBattle;
}

export function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

export function mergeBattleDelta(
  previous: string,
  delta: string,
  replay: boolean,
  chunkIndex: number | null,
): string {
  if (replay && (chunkIndex === null || chunkIndex === 0)) {
    return delta;
  }

  return previous + delta;
}
