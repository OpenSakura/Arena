export function isBattleBootstrapReady(authStatus: string): boolean {
  return authStatus !== "loading";
}

export function hasBattleSessionError(sessionError: string | null | undefined): boolean {
  return typeof sessionError === "string" && sessionError.length > 0;
}
