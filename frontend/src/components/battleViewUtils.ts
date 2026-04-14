import { apiGet, apiPost } from "../lib/api";

export { asRecord } from "@/lib/typeGuards";

export async function loadOrCreateBattle(
  battleId: string,
  accessToken?: string,
): Promise<unknown> {
  const headers = accessToken ? { Authorization: `Bearer ${accessToken}` } : undefined;

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
