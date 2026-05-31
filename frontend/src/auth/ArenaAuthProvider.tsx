import { createContext, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";

import { setApiCsrfToken, setApiCsrfHeaderName, setApiUnauthorizedHandler } from "@/lib/api";
import { setSseUnauthorizedHandler } from "@/lib/sse";

import {
  assertBackendSessionConfig,
  extractReturnTo,
  toBackendSessionUser,
  type BackendSessionResponse,
  type BackendSessionUser,
  type PublicAuthConfig,
  type PublicConfig,
  type SessionErrorCode,
} from "./session";

type RedirectCompatibilityArgs = {
  state?: unknown;
};

type AuthState =
  | { status: "loading" }
  | { status: "ready"; config: PublicAuthConfig; session: BackendSessionResponse; sessionError: SessionErrorCode | null }
  | { status: "error"; error: AuthBootstrapErrorDetails; sessionError: SessionErrorCode };

type AuthBootstrapErrorDetails =
  | { kind: "publicConfig"; status: number }
  | { kind: "session"; status: number }
  | { kind: "unknown" };

class AuthBootstrapRequestError extends Error {
  constructor(
    readonly kind: "publicConfig" | "session",
    readonly status: number,
  ) {
    super("auth-bootstrap-request-failed");
  }
}

export type ArenaAuthContextValue = {
  authStatus: "loading" | "authenticated" | "unauthenticated";
  isLoading: boolean;
  isAuthenticated: boolean;
  user: BackendSessionUser | null;
  csrfToken: string | null;
  sessionError: SessionErrorCode | null;
  signinRedirect: (args?: RedirectCompatibilityArgs) => Promise<void>;
  signoutRedirect: () => Promise<void>;
};

const SESSION_KEEPALIVE_INTERVAL_MS = 60_000;

export const ArenaAuthContext = createContext<ArenaAuthContextValue | null>(null);
ArenaAuthContext.displayName = "ArenaAuthContext";

export const arenaAuthNavigation = {
  to(url: string) {
    window.location.href = url;
  },
};

async function loadPublicConfig(signal: AbortSignal): Promise<PublicConfig> {
  const response = await fetch("/api/v1/public-config", {
    signal,
    headers: { Accept: "application/json" },
  });

  if (!response.ok) {
    throw new AuthBootstrapRequestError("publicConfig", response.status);
  }

  return (await response.json()) as PublicConfig;
}

export async function loadArenaPublicConfig(signal: AbortSignal): Promise<PublicConfig> {
  return loadPublicConfig(signal);
}

async function loadBackendSession(sessionPath: string, signal: AbortSignal): Promise<BackendSessionResponse> {
  const response = await fetch(sessionPath, {
    signal,
    credentials: "include",
    cache: "no-store",
    headers: { Accept: "application/json" },
  });

  if (!response.ok) {
    throw new AuthBootstrapRequestError("session", response.status);
  }

  return (await response.json()) as BackendSessionResponse;
}

function LoadingShell() {
  const { t } = useTranslation();

  return (
    <div className="min-h-screen grid place-items-center px-6">
      <div className="glass-panel p-6 text-center">
        <div className="mx-auto h-4 w-4 rounded-full shimmer bg-muted/60" />
        <p className="mt-3 text-sm text-muted-foreground">{t("auth.bootstrap.loading")}</p>
      </div>
    </div>
  );
}

function ErrorShell({ error }: { error: AuthBootstrapErrorDetails }) {
  const { t } = useTranslation();

  return (
    <div className="min-h-screen grid place-items-center px-6">
      <div className="glass-panel-accent max-w-md p-6 text-center">
        <p className="text-sm font-medium text-destructive">{formatAuthBootstrapError(error, t)}</p>
        <p className="mt-2 text-xs text-muted-foreground">{t("auth.bootstrap.refresh")}</p>
      </div>
    </div>
  );
}

function buildLoginUrl(loginPath: string, returnTo: string) {
  return `${loginPath}?returnTo=${encodeURIComponent(returnTo)}`;
}

function getAuthBootstrapError(error: unknown): AuthBootstrapErrorDetails {
  if (error instanceof AuthBootstrapRequestError) {
    return { kind: error.kind, status: error.status };
  }

  return { kind: "unknown" };
}

function formatAuthBootstrapError(error: AuthBootstrapErrorDetails, t: TFunction) {
  if (error.kind === "publicConfig") {
    return t("auth.bootstrap.errors.publicConfig", { status: error.status });
  }

  if (error.kind === "session") {
    return t("auth.bootstrap.errors.session", { status: error.status });
  }

  return t("auth.bootstrap.errors.fallback");
}

export function ArenaAuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ status: "loading" });
  const configRef = useRef<PublicAuthConfig | null>(null);
  const csrfTokenRef = useRef<string | null>(null);
  const csrfHeaderNameRef = useRef<string>("X-CSRF-Token");

  const applySession = useCallback((config: PublicAuthConfig, session: BackendSessionResponse, sessionError: SessionErrorCode | null) => {
    const csrfToken = session.csrf_token ?? null;
    const csrfHeaderName = config.csrf_header_name?.trim() || "X-CSRF-Token";
    configRef.current = config;
    csrfTokenRef.current = csrfToken;
    csrfHeaderNameRef.current = csrfHeaderName;
    setApiCsrfHeaderName(csrfHeaderName);
    setApiCsrfToken(csrfToken);
    setState({ status: "ready", config, session, sessionError });
  }, []);

  const expireSession = useCallback(() => {
    csrfTokenRef.current = null;
    setApiCsrfToken(null);
    setState((current) => {
      if (current.status !== "ready") {
        return current;
      }

      return {
        status: "ready",
        config: current.config,
        session: {
          authenticated: false,
          is_admin: false,
          user: null,
          profile: null,
          csrf_token: null,
        },
        sessionError: "SessionExpired",
      };
    });
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;

    void (async () => {
      try {
        const publicConfig = await loadArenaPublicConfig(controller.signal);
        const authConfig = assertBackendSessionConfig(publicConfig);
        const session = await loadBackendSession(authConfig.session_path, controller.signal);
        if (active) {
          applySession(authConfig, session, null);
        }
      } catch (error) {
        if (!active || controller.signal.aborted) return;
        setApiCsrfToken(null);
        setState({
          status: "error",
          error: getAuthBootstrapError(error),
          sessionError: "SessionBootstrapFailed",
        });
      }
    })();

    return () => {
      active = false;
      controller.abort();
      setApiCsrfToken(null);
    };
  }, [applySession]);

  useEffect(() => {
    setApiUnauthorizedHandler(expireSession);
    setSseUnauthorizedHandler(expireSession);
    return () => {
      setApiUnauthorizedHandler(null);
      setSseUnauthorizedHandler(null);
    };
  }, [expireSession]);

  useEffect(() => {
    if (state.status !== "ready" || !state.session.authenticated) {
      return;
    }

    let stopped = false;
    const keepAlive = async () => {
      const config = configRef.current;
      if (!config) {
        return;
      }

      const controller = new AbortController();
      try {
        const session = await loadBackendSession(config.session_path, controller.signal);
        if (stopped) {
          return;
        }
        if (session.authenticated) {
          applySession(config, session, null);
        } else {
          expireSession();
        }
      } catch (error) {
        if (!stopped && error instanceof AuthBootstrapRequestError && error.status === 401) {
          expireSession();
        }
      }
    };

    const interval = window.setInterval(() => {
      void keepAlive();
    }, SESSION_KEEPALIVE_INTERVAL_MS);

    return () => {
      stopped = true;
      window.clearInterval(interval);
    };
  }, [applySession, expireSession, state]);

  const signinRedirect = useCallback(async (args: RedirectCompatibilityArgs = {}) => {
    const config = configRef.current;
    const returnTo = extractReturnTo(args.state);
    arenaAuthNavigation.to(buildLoginUrl(config?.login_path ?? "/api/v1/auth/login", returnTo));
  }, []);

  const signoutRedirect = useCallback(async () => {
    const config = configRef.current;
    const logoutPath = config?.logout_path ?? "/api/v1/auth/logout";
    const csrfToken = csrfTokenRef.current;
    const csrfHeaderName = csrfHeaderNameRef.current;

    try {
      await fetch(logoutPath, {
        method: "POST",
        credentials: "include",
        headers: {
          Accept: "application/json",
          ...(csrfToken ? { [csrfHeaderName]: csrfToken } : {}),
        },
      });
    } finally {
      csrfTokenRef.current = null;
      setApiCsrfToken(null);
      setState((current) => {
        if (current.status !== "ready") {
          return current;
        }

        return {
          status: "ready",
          config: current.config,
          session: {
            authenticated: false,
            is_admin: false,
            user: null,
            profile: null,
            csrf_token: null,
          },
          sessionError: null,
        };
      });
      arenaAuthNavigation.to("/");
    }
  }, []);

  const value = useMemo<ArenaAuthContextValue | null>(() => {
    if (state.status !== "ready") {
      return null;
    }

    const user = toBackendSessionUser(state.session);
    const authenticated = state.session.authenticated && user !== null;
    const csrfToken = state.session.csrf_token ?? null;

    return {
      authStatus: authenticated ? "authenticated" : "unauthenticated",
      isLoading: false,
      isAuthenticated: authenticated,
      user,
      csrfToken,
      sessionError: state.sessionError,
      signinRedirect,
      signoutRedirect,
    };
  }, [signinRedirect, signoutRedirect, state]);

  if (state.status === "loading") {
    return <LoadingShell />;
  }

  if (state.status === "error") {
    return <ErrorShell error={state.error} />;
  }

  return <ArenaAuthContext.Provider value={value}>{children}</ArenaAuthContext.Provider>;
}
