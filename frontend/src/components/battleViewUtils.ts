import { apiGet, apiPost } from "../lib/api";

export { asRecord } from "@/lib/typeGuards";

export function buildBattleAuthHeaders(
  accessToken?: string,
): Record<string, string> | undefined {
  return accessToken ? { Authorization: `Bearer ${accessToken}` } : undefined;
}

export async function loadOrCreateBattle(
  battleId: string,
  accessToken?: string,
): Promise<unknown> {
  const headers = buildBattleAuthHeaders(accessToken);

  if (battleId === "new") {
    return apiPost("/battles", {}, { headers });
  }

  return apiGet(`/battles/${encodeURIComponent(battleId)}`, { headers });
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
