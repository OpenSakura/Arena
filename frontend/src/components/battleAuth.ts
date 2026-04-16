const BATTLE_REFRESH_ERRORS = [
  "RefreshTokenMissing",
  "RefreshDiscoveryFailed",
  "RefreshTokenExpired",
  "RefreshTokenError",
] as const;

export function isBattleBootstrapReady(authStatus: string): boolean {
  return authStatus !== "loading";
}

export function hasBattleRefreshError(sessionError: string | null | undefined): boolean {
  return (
    typeof sessionError === "string"
    && BATTLE_REFRESH_ERRORS.includes(sessionError as (typeof BATTLE_REFRESH_ERRORS)[number])
  );
}
