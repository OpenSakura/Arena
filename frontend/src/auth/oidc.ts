import { WebStorageStateStore, type SignoutResponse, type User } from "oidc-client-ts";
import type { AuthProviderProps } from "react-oidc-context";

export type PublicOidcConfig = {
  issuer: string;
  client_id: string;
  scope: string;
  redirect_path: string;
  silent_redirect_path: string;
  post_logout_redirect_path: string;
};

export type PublicConfig = {
  anon_battle_turnstile_required: boolean;
  oidc: PublicOidcConfig;
};

export type SessionErrorCode =
  | "RefreshTokenMissing"
  | "RefreshDiscoveryFailed"
  | "RefreshTokenExpired"
  | "RefreshTokenError";

function toText(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (value instanceof Error) {
    return [value.name, value.message, toText((value as Error & { cause?: unknown }).cause), toText((value as Error & { innerError?: unknown }).innerError)]
      .filter(Boolean)
      .join(" ");
  }
  if (typeof value === "object") {
    const record = value as Record<string, unknown>;
    return [
      record.name,
      record.message,
      record.error,
      record.error_description,
      record.status,
      record.source,
      record.code,
      record.detail,
      record.description,
      record.innerError,
      record.cause,
    ]
      .map(toText)
      .filter(Boolean)
      .join(" ");
  }
  return "";
}

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

export function buildAbsoluteUrl(origin: string, path: string): string {
  return new URL(path, origin).toString();
}

export function createSessionStorageStore(storage: Storage = window.sessionStorage): WebStorageStateStore {
  return new WebStorageStateStore({ store: storage });
}

export function buildOidcSettings(config: PublicOidcConfig, origin: string): AuthProviderProps {
  return {
    authority: config.issuer.replace(/\/$/, ""),
    client_id: config.client_id,
    scope: config.scope,
    response_type: "code",
    redirect_uri: buildAbsoluteUrl(origin, config.redirect_path),
    silent_redirect_uri: buildAbsoluteUrl(origin, config.silent_redirect_path),
    post_logout_redirect_uri: buildAbsoluteUrl(origin, config.post_logout_redirect_path),
    automaticSilentRenew: true,
    disablePKCE: false,
    userStore: createSessionStorageStore(window.sessionStorage),
  } as AuthProviderProps;
}

export function deriveSessionError(
  error: unknown,
  user: User | null,
  accessToken: string | null,
): SessionErrorCode | null {
  if (!user) return null;
  if (!accessToken) return "RefreshTokenExpired";
  if (!error) return null;

  const text = toText(error).toLowerCase();
  if (text.includes("discover") || text.includes("well-known") || text.includes("metadata")) {
    return "RefreshDiscoveryFailed";
  }
  if (text.includes("missing") || text.includes("no refresh token")) {
    return "RefreshTokenMissing";
  }
  if (
    text.includes("invalid_grant") ||
    text.includes("expired") ||
    text.includes("login_required") ||
    text.includes("consent_required") ||
    text.includes("interaction_required") ||
    text.includes("account_selection_required")
  ) {
    return "RefreshTokenExpired";
  }
  return "RefreshTokenError";
}

export function extractSignoutReturnTo(resp: SignoutResponse | undefined): string {
  if (!resp || typeof resp !== "object") return "/";
  return extractReturnTo((resp as { state?: unknown }).state);
}
