import { useEffect, useState } from "react";

import { useAuthHeaders } from "@/hooks/useAuthHeaders";
import { apiGet } from "@/lib/api";
import { parseMeResponse } from "@/types/me";
import { SESSION_EXPIRED_MESSAGE } from "@/auth/oidc";

function isUnauthorizedMeError(error: unknown) {
  return error instanceof Error && /^GET \/me failed: 401\b/.test(error.message);
}

export function useAdminAccess() {
  const { authStatus, accessToken, headers, sessionError } = useAuthHeaders();
  const [isAdmin, setIsAdmin] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      if (authStatus === "loading") {
        setLoading(true);
        setIsAdmin(false);
        setError(null);
        return;
      }

      if (sessionError) {
        setLoading(false);
        setIsAdmin(false);
        setError(SESSION_EXPIRED_MESSAGE);
        return;
      }

      if (authStatus !== "authenticated") {
        setLoading(false);
        setIsAdmin(false);
        setError(null);
        return;
      }

      if (!accessToken || !headers) {
        setLoading(false);
        setIsAdmin(false);
        setError(SESSION_EXPIRED_MESSAGE);
        return;
      }

      setLoading(true);
      setError(null);

      try {
        const response = parseMeResponse(await apiGet("/me", { headers }));
        if (cancelled) return;

        if (!response.authenticated) {
          setIsAdmin(false);
          setLoading(false);
          setError(SESSION_EXPIRED_MESSAGE);
          return;
        }

        setIsAdmin(Boolean(response.is_admin));
        setLoading(false);
      } catch (err) {
        if (cancelled) return;
        setIsAdmin(false);
        setLoading(false);
        if (isUnauthorizedMeError(err)) {
          setError(SESSION_EXPIRED_MESSAGE);
          return;
        }
        setError(err instanceof Error ? err.message : "Failed to check admin access");
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [accessToken, authStatus, headers, sessionError]);

  return {
    isAuthenticated: authStatus === "authenticated",
    isAdmin,
    loading,
    error,
  };
}
