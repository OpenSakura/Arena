/**
 * frontend/src/hooks/useAuthHeaders.ts
 *
 * Shared compatibility hook for pages that still expect authenticated API header
 * state. Human browser API auth now uses same-origin session cookies, while CSRF
 * is applied centrally by the API helper from the in-memory token provider.
 */

import { useArenaAuth } from "@/hooks/useArenaAuth";

export function useAuthHeaders() {
  const { authStatus, csrfToken, sessionError, user } = useArenaAuth();

  return {
    authStatus,
    csrfToken,
    sessionError,
    user,
  };
}
