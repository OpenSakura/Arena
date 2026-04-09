/**
 * frontend/src/components/Header.tsx
 *
 * Top navigation bar with active link highlighting, mobile menu, and sakura branding.
 */

"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { signIn, signOut, useSession } from "next-auth/react";
import { useEffect, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Button } from "@/components/ui/button";
import { ThemeToggle } from "@/components/ThemeToggle";

type NavLink = {
  href: string;
  label: string;
  prefix?: string;
};

const NAV_LINKS: NavLink[] = [
  { href: "/battle/new", label: "Battle", prefix: "/battle" },
  { href: "/leaderboard", label: "Leaderboard" },
  { href: "/onboarding", label: "Profile" },
  { href: "/admin/models", label: "Admin", prefix: "/admin" },
];

function SakuraIcon({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" className={className} aria-hidden>
      <path
        d="M12 2C12 2 9.5 6.5 9.5 10C9.5 12.5 10.5 14 12 15C13.5 14 14.5 12.5 14.5 10C14.5 6.5 12 2 12 2Z"
        fill="currentColor"
        opacity="0.85"
      />
      <path
        d="M12 15C10.5 16 8 16.5 5.5 15.5C3 14.5 2 12 2 12C2 12 4 14.5 7 15C9 15.3 11 14.8 12 15Z"
        fill="currentColor"
        opacity="0.6"
      />
      <path
        d="M12 15C13.5 16 16 16.5 18.5 15.5C21 14.5 22 12 22 12C22 12 20 14.5 17 15C15 15.3 13 14.8 12 15Z"
        fill="currentColor"
        opacity="0.6"
      />
      <circle cx="12" cy="15" r="1.5" fill="currentColor" opacity="0.9" />
    </svg>
  );
}

export function Header() {
  const { data: session, status } = useSession();
  const isAuthenticated = status === "authenticated" && Boolean(session?.accessToken);
  const pathname = usePathname() ?? "/";
  const [mobileOpen, setMobileOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);

  // Auto-close mobile menu on route change.
  useEffect(() => {
    setMobileOpen(false);
  }, [pathname]);

  useEffect(() => {
    function handleScroll() {
      setScrolled(window.scrollY > 10);
    }
    window.addEventListener("scroll", handleScroll, { passive: true });
    handleScroll();
    return () => window.removeEventListener("scroll", handleScroll);
  }, []);

  function isActive(link: NavLink) {
    if (link.prefix) return pathname.startsWith(link.prefix);
    return pathname === link.href || pathname.startsWith(link.href + "/");
  }

  return (
    <header className={`sticky top-0 z-50 w-full border-b transition-all duration-300 ${
      scrolled
        ? "border-border/60 bg-background/80 backdrop-blur-2xl shadow-lg shadow-black/10"
        : "border-border/30 bg-background/40 backdrop-blur-xl"
    }`}>
      <div className="mx-auto flex h-14 max-w-7xl items-center justify-between px-6">
        {/* Logo */}
        <Link href="/" className="flex items-center gap-2 group">
          <div className="flex h-7 w-7 items-center justify-center rounded-lg border border-primary/20 bg-primary/[0.08] transition-colors group-hover:bg-primary/[0.12] group-hover:border-primary/30">
            <SakuraIcon className="h-4 w-4 text-primary" />
          </div>
          <span className="text-lg font-extrabold tracking-tight bg-gradient-to-r from-sakura-deep via-sakura to-sakura-soft bg-clip-text text-transparent transition-opacity group-hover:opacity-80">
            OpenSakura Arena
          </span>
        </Link>

        {/* Desktop nav */}
        <nav className="hidden md:flex items-center gap-1 text-sm font-medium">
          {NAV_LINKS.filter((link) => !link.prefix || isAuthenticated).map((link) => (
            <Link
              key={link.href}
              href={link.href}
              className={`relative rounded-lg px-3 py-1.5 transition-all duration-200 ${
                isActive(link)
                  ? "text-primary bg-primary/8"
                  : "text-muted-foreground hover:text-foreground hover:bg-foreground/5"
              }`}
            >
              {link.label}
              {isActive(link) && (
                <motion.span
                  layoutId="nav-indicator"
                  className="absolute inset-x-2 -bottom-[9px] h-px bg-primary/60"
                  transition={{ type: "spring", stiffness: 500, damping: 35 }}
                />
              )}
            </Link>
          ))}

          <div className="h-5 w-px bg-border mx-2" />

          <ThemeToggle className="mx-1" />

          {status === "loading" ? (
            <div className="h-8 w-20 rounded-full shimmer bg-muted/60" />
          ) : isAuthenticated ? (
            <div className="flex items-center gap-3">
              <span className="text-xs text-muted-foreground max-w-[120px] truncate">
                {(session?.user?.name || session?.user?.email || "Signed in") as string}
              </span>
              <Button
                variant="outline"
                size="sm"
                className="h-7 rounded-full px-3 text-xs border-border/50 hover:border-border transition-colors"
                onClick={() => void signOut({ callbackUrl: "/" })}
              >
                Logout
              </Button>
            </div>
          ) : (
            <Button
              variant="default"
              size="sm"
              className="h-7 rounded-full px-4 text-xs"
              onClick={() => void signIn("authentik")}
            >
              Login
            </Button>
          )}
        </nav>

        {/* Mobile hamburger */}
        <button
          type="button"
          className="md:hidden flex flex-col gap-1 p-2"
          onClick={() => setMobileOpen(!mobileOpen)}
          aria-label="Toggle menu"
          aria-expanded={mobileOpen}
        >
          <span className={`block h-0.5 w-5 bg-foreground transition-all ${mobileOpen ? "rotate-45 translate-y-1.5" : ""}`} />
          <span className={`block h-0.5 w-5 bg-foreground transition-all ${mobileOpen ? "opacity-0" : ""}`} />
          <span className={`block h-0.5 w-5 bg-foreground transition-all ${mobileOpen ? "-rotate-45 -translate-y-1.5" : ""}`} />
        </button>
      </div>

      {/* Mobile menu */}
      <AnimatePresence>
        {mobileOpen && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.2, ease: "easeInOut" }}
            className="md:hidden border-t border-border/50 bg-background/95 backdrop-blur-xl overflow-hidden"
          >
            <div className="px-6 py-4 space-y-1">
              {NAV_LINKS.filter((link) => !link.prefix || isAuthenticated).map((link) => (
                <Link
                  key={link.href}
                  href={link.href}
                  onClick={() => setMobileOpen(false)}
                  className={`block rounded-lg px-3 py-2.5 text-sm font-medium transition-all ${
                    isActive(link)
                      ? "text-primary bg-primary/8"
                      : "text-muted-foreground hover:text-foreground hover:bg-foreground/5"
                  }`}
                >
                  {link.label}
                </Link>
              ))}
              <div className="divider-fade my-3" />

              <div className="flex items-center justify-between py-2">
                <span className="text-xs text-muted-foreground">Theme</span>
                <ThemeToggle />
              </div>

              {status === "loading" ? null : isAuthenticated ? (
                <div className="flex items-center justify-between py-2">
                  <span className="text-xs text-muted-foreground">
                    {(session?.user?.name || session?.user?.email || "Signed in") as string}
                  </span>
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-7 rounded-full px-3 text-xs"
                    onClick={() => void signOut({ callbackUrl: "/" })}
                  >
                    Logout
                  </Button>
                </div>
              ) : (
                <Button
                  variant="default"
                  size="sm"
                  className="w-full rounded-full text-xs"
                  onClick={() => void signIn("authentik")}
                >
                  Login
                </Button>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </header>
  );
}
