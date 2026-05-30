import type { MeProfile, MeUser } from "@/types/me";

export type PublicAuthConfig = {
  mode: "backend_session";
  login_path: string;
  logout_path: string;
  session_path: string;
  csrf_header_name?: string;
};

export type PublicConfig = {
  anon_battle_turnstile_required: boolean;
  auth: PublicAuthConfig;
};

export type BackendSessionResponse = {
  authenticated: boolean;
  is_admin?: boolean;
  user?: MeUser | null;
  profile?: MeProfile | null;
  csrf_token?: string | null;
};

export type BackendSessionProfile = MeProfile & {
  name?: string | null;
  preferred_username?: string | null;
  email?: string | null;
};

export type BackendSessionUser = {
  id: string;
  oidcIssuer: string;
  oidcSub: string;
  createdAt: string;
  isAdmin: boolean;
  profile: BackendSessionProfile;
};

export type SessionErrorCode =
  | "SessionBootstrapFailed"
  | "SessionExpired"
  | "SessionLogoutFailed";

export const SESSION_EXPIRED_MESSAGE = "Your session has expired. Please log in again.";

export function normalizeReturnTo(value: unknown): string {
  if (typeof value !== "string") return "/";

  const trimmed = value.trim();
  if (!trimmed) return "/";

  try {
    const url = new URL(trimmed, window.location.origin);
    if (url.origin !== window.location.origin) return "/";
    return `${url.pathname}${url.search}${url.hash}` || "/";
  } catch {
    return "/";
  }
}

export function extractReturnTo(state: unknown): string {
  if (state && typeof state === "object") {
    const record = state as Record<string, unknown>;
    if ("returnTo" in record) {
      return normalizeReturnTo(record.returnTo);
    }
  }
  return normalizeReturnTo(state);
}

export function toBackendSessionUser(session: BackendSessionResponse): BackendSessionUser | null {
  if (!session.authenticated || !session.user) {
    return null;
  }

  const backendProfile = session.profile ?? {};

  return {
    id: session.user.id,
    oidcIssuer: session.user.oidc_issuer,
    oidcSub: session.user.oidc_sub,
    createdAt: session.user.created_at,
    isAdmin: Boolean(session.is_admin),
    profile: {
      ...backendProfile,
      name: backendProfile.display_name ?? null,
      preferred_username: session.user.oidc_sub,
      email: null,
    },
  };
}

export function assertBackendSessionConfig(config: PublicConfig): PublicAuthConfig {
  if (config.auth?.mode !== "backend_session") {
    throw new Error("Unsupported authentication mode");
  }

  const { login_path: loginPath, logout_path: logoutPath, session_path: sessionPath } = config.auth;
  if (!loginPath || !logoutPath || !sessionPath) {
    throw new Error("Backend session authentication paths are missing");
  }

  if (config.auth.csrf_header_name !== undefined && !config.auth.csrf_header_name.trim()) {
    throw new Error("Backend session CSRF header name is invalid");
  }

  return config.auth;
}
