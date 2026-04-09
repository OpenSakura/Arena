"use client";

import type { ReactNode } from "react";

import { SessionProvider, signIn, useSession } from "next-auth/react";

import { ThemeProvider } from "@/components/ThemeProvider";

function SessionErrorBanner() {
  const { data: session } = useSession();

  if (!session?.error) return null;

  return (
    <div
      role="alert"
      className="sticky top-0 z-[60] flex items-center justify-center gap-3 bg-destructive/90 px-4 py-2 text-sm text-destructive-foreground backdrop-blur"
    >
      <span>Your session has expired.</span>
      <button
        type="button"
        onClick={() => void signIn("authentik")}
        className="underline underline-offset-2 font-medium hover:opacity-80"
      >
        Sign in again
      </button>
    </div>
  );
}

export function Providers({ children }: { children: ReactNode }) {
  return (
    <ThemeProvider attribute="class" defaultTheme="system" enableSystem>
      <SessionProvider refetchInterval={4 * 60} refetchOnWindowFocus={true}>
        <SessionErrorBanner />
        {children}
      </SessionProvider>
    </ThemeProvider>
  );
}
