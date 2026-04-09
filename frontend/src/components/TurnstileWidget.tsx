/**
 * frontend/src/components/TurnstileWidget.tsx
 *
 * Cloudflare Turnstile widget wrapper.
 *
 * Notes:
 * - We use explicit render mode so React controls when the widget mounts.
 * - Tokens can expire; callers should clear stored token on expire/error.
 */

"use client";

import Script from "next/script";
import { useEffect, useRef, useState } from "react";

declare global {
  interface Window {
    turnstile?: {
      render: (container: HTMLElement, options: Record<string, unknown>) => string;
      reset: (widgetId?: string) => void;
      remove?: (widgetId: string) => void;
    };
  }
}

type TurnstileTheme = "auto" | "light" | "dark";

export function TurnstileWidget({
  siteKey,
  theme = "auto",
  action,
  cData,
  onToken,
  onExpire,
  onError,
}: {
  siteKey: string;
  theme?: TurnstileTheme;
  action?: string;
  cData?: string;
  onToken: (token: string) => void;
  onExpire?: () => void;
  onError?: (message: string) => void;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const widgetIdRef = useRef<string | null>(null);

  const onTokenRef = useRef(onToken);
  const onExpireRef = useRef(onExpire);
  const onErrorRef = useRef(onError);

  const [scriptReady, setScriptReady] = useState(false);
  const [scriptTimedOut, setScriptTimedOut] = useState(false);

  // Timeout for Turnstile script load — fire onError so the UI can surface
  // the problem instead of showing "Loading Turnstile…" indefinitely.
  const SCRIPT_LOAD_TIMEOUT_MS = 15_000;

  useEffect(() => {
    if (scriptReady || scriptTimedOut) return;

    const timer = setTimeout(() => {
      if (!scriptReady) {
        setScriptTimedOut(true);
        onErrorRef.current?.("Turnstile script failed to load — please check your network or ad-blocker");
      }
    }, SCRIPT_LOAD_TIMEOUT_MS);

    return () => clearTimeout(timer);
  }, [scriptReady, scriptTimedOut]);

  useEffect(() => {
    onTokenRef.current = onToken;
    onExpireRef.current = onExpire;
    onErrorRef.current = onError;
  }, [onToken, onExpire, onError]);

  useEffect(() => {
    // If the script is already present (e.g., client navigation), mark ready.
    if (typeof window !== "undefined" && window.turnstile?.render) {
      setScriptReady(true);
    }
  }, []);

  useEffect(() => {
    if (!scriptReady) return;
    if (!siteKey) return;

    const container = containerRef.current;
    const turnstile = window.turnstile;
    if (!container || !turnstile?.render) return;

    // Re-rendering without removing can create stacked iframes.
    if (widgetIdRef.current && turnstile.remove) {
      try {
        turnstile.remove(widgetIdRef.current);
      } catch {
        // Best-effort; Turnstile may throw if widget already removed.
      }
      widgetIdRef.current = null;
    }
    container.innerHTML = "";

    widgetIdRef.current = turnstile.render(container, {
      sitekey: siteKey,
      theme,
      ...(action ? { action } : {}),
      ...(cData ? { cData } : {}),
      callback: (token: unknown) => {
        if (typeof token === "string") onTokenRef.current(token);
      },
      "expired-callback": () => {
        onExpireRef.current?.();
      },
      "timeout-callback": () => {
        onExpireRef.current?.();
      },
      "error-callback": () => {
        onErrorRef.current?.("Turnstile error");
      },
    });

    return () => {
      if (widgetIdRef.current && turnstile.remove) {
        try {
          turnstile.remove(widgetIdRef.current);
        } catch {
          // Best-effort cleanup.
        }
      }
      widgetIdRef.current = null;
    };
  }, [scriptReady, siteKey, theme, action, cData]);

  return (
    <div style={{ display: "grid", gap: 8 }}>
      <Script
        id="turnstile-script"
        src="https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit"
        strategy="afterInteractive"
        onLoad={() => setScriptReady(true)}
      />

      <div ref={containerRef} />

      {!scriptReady ? (
        <div style={{ fontSize: 13, color: "var(--muted)" }}>
          {scriptTimedOut ? "Turnstile failed to load" : "Loading Turnstile..."}
        </div>
      ) : null}
    </div>
  );
}
