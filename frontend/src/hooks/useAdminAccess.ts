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

type MeResponse = {
  authenticated: boolean;
  is_admin: boolean;
};

export function useAdminAccess() {
  const { data: session, status } = useSession();
  const isAuthenticated = status === "authenticated" && Boolean(session?.accessToken);

  const [isAdmin, setIsAdmin] = useState<boolean | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (status === "loading") {
      setLoading(true);
      return;
    }

    if (!isAuthenticated) {
      setIsAdmin(false);
      setLoading(false);
      return;
    }

    let cancelled = false;
    setLoading(true);

    apiGet("/me", {
      headers: { Authorization: `Bearer ${session.accessToken!}` },
    })
      .then((data) => {
        if (cancelled) return;
        const me = data as MeResponse;
        setIsAdmin(Boolean(me?.is_admin));
      })
      .catch(() => {
        if (!cancelled) setIsAdmin(false);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [isAuthenticated, session?.accessToken, status]);

  return { isAuthenticated, isAdmin, loading };
}
