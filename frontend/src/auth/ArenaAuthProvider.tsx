import { createContext, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { setApiCsrfToken } from "@/lib/api";

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
  | { status: "error"; message: string; sessionError: SessionErrorCode };

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
    throw new Error(`Failed to load public config (${response.status})`);
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
    headers: { Accept: "application/json" },
  });

  if (!response.ok) {
    throw new Error(`Failed to load auth session (${response.status})`);
  }

  return (await response.json()) as BackendSessionResponse;
}

function LoadingShell() {
  return (
    <div className="min-h-screen grid place-items-center px-6">
      <div className="glass-panel p-6 text-center">
        <div className="mx-auto h-4 w-4 rounded-full shimmer bg-muted/60" />
        <p className="mt-3 text-sm text-muted-foreground">Loading authentication…</p>
      </div>
    </div>
  );
}

function ErrorShell({ message }: { message: string }) {
  return (
    <div className="min-h-screen grid place-items-center px-6">
      <div className="glass-panel-accent max-w-md p-6 text-center">
        <p className="text-sm font-medium text-destructive">{message}</p>
        <p className="mt-2 text-xs text-muted-foreground">Please refresh the page to try again.</p>
      </div>
    </div>
  );
}

function buildLoginUrl(loginPath: string, returnTo: string) {
  return `${loginPath}?returnTo=${encodeURIComponent(returnTo)}`;
}

function getSessionErrorMessage(error: unknown) {
  return error instanceof Error ? error.message : "Failed to load authentication session";
}

export function ArenaAuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ status: "loading" });
  const configRef = useRef<PublicAuthConfig | null>(null);
  const csrfTokenRef = useRef<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;

    void (async () => {
      try {
        const publicConfig = await loadArenaPublicConfig(controller.signal);
        const authConfig = assertBackendSessionConfig(publicConfig);
        const session = await loadBackendSession(authConfig.session_path, controller.signal);
        if (active) {
          const csrfToken = session.csrf_token ?? null;
          configRef.current = authConfig;
          csrfTokenRef.current = csrfToken;
          setApiCsrfToken(csrfToken);
          setState({ status: "ready", config: authConfig, session, sessionError: null });
        }
      } catch (error) {
        if (!active || controller.signal.aborted) return;
        setApiCsrfToken(null);
        setState({
          status: "error",
          message: getSessionErrorMessage(error),
          sessionError: "SessionBootstrapFailed",
        });
      }
    })();

    return () => {
      active = false;
      controller.abort();
      setApiCsrfToken(null);
    };
  }, []);

  const signinRedirect = useCallback(async (args: RedirectCompatibilityArgs = {}) => {
    const config = configRef.current;
    const returnTo = extractReturnTo(args.state);
    arenaAuthNavigation.to(buildLoginUrl(config?.login_path ?? "/api/v1/auth/login", returnTo));
  }, []);

  const signoutRedirect = useCallback(async () => {
    const config = configRef.current;
    const logoutPath = config?.logout_path ?? "/api/v1/auth/logout";
    const csrfToken = csrfTokenRef.current;

    try {
      await fetch(logoutPath, {
        method: "POST",
        credentials: "include",
        headers: {
          Accept: "application/json",
          ...(csrfToken ? { "X-CSRF-Token": csrfToken } : {}),
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
    return <ErrorShell message={state.message} />;
  }

  return <ArenaAuthContext.Provider value={value}>{children}</ArenaAuthContext.Provider>;
}
