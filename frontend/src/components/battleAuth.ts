export function isBattleBootstrapReady(authStatus: string): boolean {
  return authStatus !== "loading";
}
