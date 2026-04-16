/**
 * frontend/src/hooks/useAuthHeaders.ts
 *
 * Shared hook for pages that need authenticated API headers.
 */

import { useArenaAuth } from "@/hooks/useArenaAuth";

export function useAuthHeaders() {
  const { headers, headersRef, accessTokenRef, authStatus, accessToken, sessionError } = useArenaAuth();

  return {
    headers,
    headersRef,
    accessTokenRef,
    authStatus,
    accessToken,
    sessionError,
  };
}
