import { apiGet, apiPost } from "../lib/api";

export { asRecord } from "@/lib/typeGuards";

export function buildBattleAuthHeaders(): undefined {
  return undefined;
}

export async function loadOrCreateBattle(
  battleId: string,
): Promise<unknown> {
  if (battleId === "new") {
    return apiPost("/battles", {});
  }

  return apiGet(`/battles/${encodeURIComponent(battleId)}`);
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
