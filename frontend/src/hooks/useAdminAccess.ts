/**
 * frontend/src/hooks/useAdminAccess.ts
 *
 * Hook to centralize checking if the current user is an admin.
 * Depends on next-auth session and the backend /me endpoint.
 */

"use client";

import { useEffect, useState } from "react";
import { useSession } from "next-auth/react";

import { apiGet } from "@/lib/api";
import { parseMeResponse } from "@/types/me";

const ADMIN_ACCESS_CACHE_TTL_MS = 5_000;

let meRequestCache:
  | {
      accessToken: string;
      promise: Promise<ReturnType<typeof parseMeResponse>>;
      timestamp: number;
    }
  | null = null;

function loadMe(accessToken: string) {
  const now = Date.now();
  if (
    meRequestCache &&
    meRequestCache.accessToken === accessToken &&
    now - meRequestCache.timestamp < ADMIN_ACCESS_CACHE_TTL_MS
  ) {
    return meRequestCache.promise;
  }

  const promise = apiGet("/me", {
    headers: { Authorization: `Bearer ${accessToken}` },
  })
    .then((data) => parseMeResponse(data))
    .catch((error: unknown) => {
      if (meRequestCache?.promise === promise) {
        meRequestCache = null;
      }
      throw error;
    });

  meRequestCache = {
    accessToken,
    promise,
    timestamp: now,
  };

  return promise;
}

export function useAdminAccess() {
  const { data: session, status } = useSession();
  const isAuthenticated = status === "authenticated" && Boolean(session?.accessToken);

  const [isAdmin, setIsAdmin] = useState<boolean | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (status === "loading") {
      setLoading(true);
      setError(null);
      return;
    }

    if (!isAuthenticated) {
      setIsAdmin(false);
      setLoading(false);
      setError(null);
      return;
    }

    let cancelled = false;
    setLoading(true);
    setError(null);

    loadMe(session.accessToken!)
      .then((me) => {
        if (cancelled) return;
        setIsAdmin(Boolean(me?.is_admin));
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setIsAdmin(false);
        setError(err instanceof Error ? err.message : "Failed to load admin access");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [isAuthenticated, session?.accessToken, status]);

  return { isAuthenticated, isAdmin, loading, error };
}
