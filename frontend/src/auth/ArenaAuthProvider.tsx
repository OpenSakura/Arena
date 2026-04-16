import { createContext, useEffect, useMemo, useRef, useState, type MutableRefObject, type ReactNode } from "react";
import { AuthProvider as OidcAuthProvider, useAuth, type AuthProviderProps } from "react-oidc-context";
import type { SigninRedirectArgs, SignoutRedirectArgs, User } from "oidc-client-ts";

import {
  buildOidcSettings,
  deriveSessionError,
  extractReturnTo,
  type PublicConfig,
  type SessionErrorCode,
} from "./oidc";

export type ArenaAuthContextValue = {
  authStatus: "loading" | "authenticated" | "unauthenticated";
  isLoading: boolean;
  isAuthenticated: boolean;
  user: User | null;
  accessToken: string | null;
  sessionError: SessionErrorCode | null;
  headers: Record<string, string> | undefined;
  headersRef: MutableRefObject<Record<string, string> | undefined>;
  accessTokenRef: MutableRefObject<string | null>;
  signinRedirect: (args?: SigninRedirectArgs) => Promise<void>;
  signoutRedirect: (args?: SignoutRedirectArgs) => Promise<void>;
};

export const ArenaAuthContext = createContext<ArenaAuthContextValue | null>(null);
ArenaAuthContext.displayName = "ArenaAuthContext";

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

export function finishSpaRedirect(path: string) {
  window.history.replaceState({}, document.title, path);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

export function handleSigninCallback(user?: Pick<User, "state"> | null) {
  const returnTo = extractReturnTo(user?.state);
  finishSpaRedirect(returnTo);
}

export function handleSignoutCallback() {
  finishSpaRedirect("/");
}

export function matchArenaSignoutCallback(args: Pick<URLSearchParams, never> & { post_logout_redirect_uri?: string | null }) {
  if (!args.post_logout_redirect_uri) return false;
  const callbackUrl = new URL(args.post_logout_redirect_uri);
  return callbackUrl.origin === window.location.origin && callbackUrl.pathname === window.location.pathname;
}

function AuthBridge({ children }: { children: ReactNode }) {
  const auth = useAuth();
  const accessToken = auth.user?.access_token ?? null;
  const headers = useMemo(
    () => (accessToken ? { Authorization: `Bearer ${accessToken}` } : undefined),
    [accessToken],
  );

  const accessTokenRef = useRef<string | null>(accessToken);
  const headersRef = useRef<Record<string, string> | undefined>(headers);

  useEffect(() => {
    accessTokenRef.current = accessToken;
  }, [accessToken]);

  useEffect(() => {
    headersRef.current = headers;
  }, [headers]);

  const isBusy = auth.isLoading || Boolean(auth.activeNavigator);
  const value = useMemo<ArenaAuthContextValue>(() => {
    const authenticated = auth.isAuthenticated;
    return {
      authStatus: isBusy ? "loading" : authenticated ? "authenticated" : "unauthenticated",
      isLoading: isBusy,
      isAuthenticated: authenticated,
      user: auth.user ?? null,
      accessToken,
      sessionError: deriveSessionError(auth.error, auth.user ?? null, accessToken),
      headers,
      headersRef,
      accessTokenRef,
      signinRedirect: (args: SigninRedirectArgs = {}) => auth.signinRedirect(args),
      signoutRedirect: (args: SignoutRedirectArgs = {}) => auth.signoutRedirect(args),
    };
  }, [accessToken, auth.activeNavigator, auth.error, auth.isAuthenticated, auth.isLoading, auth.signinRedirect, auth.signoutRedirect, auth.user, headers, headersRef, accessTokenRef]);

  return <ArenaAuthContext.Provider value={value}>{children}</ArenaAuthContext.Provider>;
}

export function ArenaAuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<
    | { status: "loading" }
    | { status: "ready"; config: PublicConfig }
    | { status: "error"; message: string }
  >({ status: "loading" });

  useEffect(() => {
    const controller = new AbortController();
    let active = true;

    void (async () => {
      try {
        const config = await loadArenaPublicConfig(controller.signal);
        if (active) {
          setState({ status: "ready", config });
        }
      } catch (error) {
        if (!active || controller.signal.aborted) return;
        setState({
          status: "error",
          message: error instanceof Error ? error.message : "Failed to load authentication settings",
        });
      }
    })();

    return () => {
      active = false;
      controller.abort();
    };
  }, []);

  if (state.status === "loading") {
    return <LoadingShell />;
  }

  if (state.status === "error") {
    return <ErrorShell message={state.message} />;
  }

  const settings = buildOidcSettings(state.config.oidc, window.location.origin);

  return (
    <OidcAuthProvider
      {...settings}
      onSigninCallback={handleSigninCallback as AuthProviderProps["onSigninCallback"]}
      onSignoutCallback={handleSignoutCallback}
      matchSignoutCallback={matchArenaSignoutCallback as AuthProviderProps["matchSignoutCallback"]}
    >
      <AuthBridge>{children}</AuthBridge>
    </OidcAuthProvider>
  );
}
