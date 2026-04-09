/**
 * frontend/src/app/admin/layout.tsx
 *
 * Shared layout for admin pages with sub-navigation tabs.
 */

"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useSession } from "next-auth/react";
import type { ReactNode } from "react";

const ADMIN_TABS = [
  { href: "/admin/models", label: "Models", icon: "M" },
  { href: "/admin/prompts", label: "Prompts", icon: "P" },
  { href: "/admin/tasks", label: "Tasks", icon: "T" },
] as const;

export default function AdminLayout({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const { data: session, status } = useSession();
  const isAuthed = status === "authenticated" && Boolean(session?.accessToken);

  // While auth is loading, show a skeleton to avoid flashing content.
  if (status === "loading") {
    return (
      <div className="grid gap-6">
        <div className="glass-panel p-6">
          <div className="h-4 w-40 rounded shimmer bg-muted/60" />
        </div>
      </div>
    );
  }

  // Non-authenticated users see a clear message instead of admin forms.
  if (!isAuthed) {
    return (
      <div className="grid gap-6">
        <div className="glass-panel-accent p-6 text-center">
          <p className="text-sm text-muted-foreground">
            You must be logged in with an admin account to access this area.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="grid gap-6">
      {/* Admin header with tabs */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl border border-primary/15 bg-primary/[0.08]">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="h-4.5 w-4.5 text-primary/80" aria-hidden>
              <circle cx="12" cy="12" r="3" />
              <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z" />
            </svg>
          </div>
          <h1 className="heading-gradient text-2xl">Admin</h1>
        </div>
        <nav className="flex items-center gap-1 rounded-xl border border-border/50 bg-background/30 p-1 backdrop-blur">
          {ADMIN_TABS.map((tab) => {
            const active = pathname.startsWith(tab.href);
            return (
              <Link
                key={tab.href}
                href={tab.href}
                className={`rounded-lg px-4 py-1.5 text-sm font-medium transition-all duration-200 ${
                  active
                    ? "bg-primary/10 text-primary shadow-sm"
                    : "text-muted-foreground hover:text-foreground hover:bg-foreground/5"
                }`}
              >
                {tab.label}
              </Link>
            );
          })}
        </nav>
      </div>

      <div className="divider-fade" />

      {/* Page content */}
      {children}
    </div>
  );
}
