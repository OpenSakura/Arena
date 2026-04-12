import { apiGet, apiPost } from "../lib/api";

export { asRecord } from "@/lib/typeGuards";

export async function loadOrCreateBattle(
  battleId: string,
  accessToken?: string,
  turnstileToken?: string,
): Promise<unknown> {
  const headers = accessToken ? { Authorization: `Bearer ${accessToken}` } : undefined;

  if (battleId === "new") {
    const body: Record<string, unknown> = {};
    if (turnstileToken) {
      body.turnstile_token = turnstileToken;
    }
    return apiPost("/battles", body, { headers });
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
