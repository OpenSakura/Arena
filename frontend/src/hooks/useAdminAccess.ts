import { useAuthHeaders } from "@/hooks/useAuthHeaders";
import { SESSION_EXPIRED_MESSAGE } from "@/auth/session";

export function useAdminAccess() {
  const { authStatus, sessionError, user } = useAuthHeaders();
  const isAuthenticated = authStatus === "authenticated";

  return {
    isAuthenticated,
    isAdmin: isAuthenticated ? Boolean(user?.isAdmin) : false,
    loading: authStatus === "loading",
    error: sessionError ? SESSION_EXPIRED_MESSAGE : null,
  };
}
