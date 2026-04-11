/**
 * frontend/src/hooks/useAuthHeaders.ts
 *
 * Shared hook for admin pages that need authenticated API headers.
 * Encapsulates the session → access token → Authorization header pattern
 * that was previously copy-pasted across every admin page.
 */

"use client";

import { useEffect, useMemo, useRef } from "react";
import { useSession } from "next-auth/react";

export function useAuthHeaders() {
  const { data: session, status: authStatus } = useSession();
  const accessToken = session?.accessToken;

  const accessTokenRef = useRef(accessToken);
  useEffect(() => {
    accessTokenRef.current = accessToken;
  }, [accessToken]);

  const headers = useMemo(() => {
    return accessToken
      ? { Authorization: `Bearer ${accessToken}` }
      : undefined;
  }, [accessToken]);

  return { headers, accessTokenRef, authStatus, accessToken };
}
