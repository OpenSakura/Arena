import { Link, useLocation } from "react-router-dom";
import { useEffect, useState, useId } from "react";
import { useTranslation } from "react-i18next";
import { motion, AnimatePresence } from "framer-motion";

import { ThemeToggle } from "@/components/ThemeToggle";
import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { Button } from "@/components/ui/button";
import { SakuraIcon } from "@/components/icons/SakuraIcon";
import { useAdminAccess } from "@/hooks/useAdminAccess";
import { useArenaAuth } from "@/hooks/useArenaAuth";
import type { BackendSessionUser } from "@/auth/session";

type NavLink = {
  href: string;
  labelKey: string;
  prefix?: string;
  adminRequired?: boolean;
  authRequired?: boolean;
};

const NAV_LINKS: NavLink[] = [
  { href: "/battle/new", labelKey: "nav.battle", prefix: "/battle", authRequired: true },
  { href: "/leaderboard", labelKey: "nav.leaderboard" },
  { href: "/onboarding", labelKey: "nav.onboarding", authRequired: true },
  { href: "/admin/models", labelKey: "nav.admin", prefix: "/admin", adminRequired: true },
];

function getUserLabel(user: BackendSessionUser | null, fallback: string) {
  if (!user) return fallback;
  return user.profile.email ?? user.profile.display_name ?? user.profile.name ?? user.profile.preferred_username ?? fallback;
}

export function Header() {
  const { t } = useTranslation();
  const auth = useArenaAuth();
  const { isAdmin } = useAdminAccess();
  const location = useLocation();
  const pathname = location.pathname;
  const search = location.search;
  const hash = location.hash;
  const returnTo = `${pathname}${search}${hash}` || "/";
  const [mobileOpen, setMobileOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);
  const mobileMenuId = useId();

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

  const identity = getUserLabel(auth.user, t("auth.signedIn"));

  function AuthButtonGroup({ mobile = false }: { mobile?: boolean }) {
    if (auth.authStatus === "loading") {
      return <div className={`h-8 ${mobile ? "w-full" : "w-24"} rounded shimmer bg-muted/60`} />;
    }

    if (auth.authStatus === "authenticated" && auth.user) {
      return (
        <div className={`flex ${mobile ? "flex-col items-stretch gap-2" : "items-center gap-3"}`}>
          <span className="max-w-40 truncate text-xs text-muted-foreground">{identity}</span>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => void auth.signoutRedirect()}
            className={mobile ? "justify-start" : undefined}
          >
            {t("auth.logout")}
          </Button>
        </div>
      );
    }

    return (
      <Button
        type="button"
        size="sm"
        onClick={() => void auth.signinRedirect({ state: { returnTo } })}
        className={mobile ? "justify-start" : undefined}
      >
        {t("auth.login")}
      </Button>
    );
  }

  return (
    <header className={`sticky top-0 z-50 w-full border-b transition-all duration-300 ${
      scrolled
        ? "border-border/60 bg-background/80 backdrop-blur-2xl shadow-lg shadow-black/10"
        : "border-border/30 bg-background/40 backdrop-blur-xl"
    }`}>
      <div className="mx-auto flex h-14 max-w-7xl items-center justify-between px-6">
        <Link to="/" className="flex items-center gap-2 group">
          <div className="flex h-7 w-7 items-center justify-center rounded-lg border border-primary/20 bg-primary/[0.08] transition-colors group-hover:bg-primary/[0.12] group-hover:border-primary/30">
            <SakuraIcon className="h-4 w-4 text-primary" />
          </div>
          <span className="text-lg font-extrabold tracking-tight bg-gradient-to-r from-sakura-deep via-sakura to-sakura-soft bg-clip-text text-transparent transition-opacity group-hover:opacity-80">
            OpenSakura Arena
          </span>
        </Link>

        <nav className="hidden md:flex items-center gap-1 text-sm font-medium">
          {NAV_LINKS.filter((link) => {
            if (link.adminRequired && !isAdmin) return false;
            if (link.authRequired && auth.authStatus !== "authenticated") return false;
            return true;
          }).map((link) => (
            <Link
              key={link.href}
              to={link.href}
              className={`relative rounded-lg px-3 py-1.5 transition-all duration-200 ${
                isActive(link)
                  ? "text-primary bg-primary/8"
                  : "text-muted-foreground hover:text-foreground hover:bg-foreground/5"
              }`}
            >
              {t(link.labelKey)}
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

          <div className="mr-1">
            <AuthButtonGroup />
          </div>

          <LanguageSwitcher className="mx-1" />
          <ThemeToggle className="mx-1" />
        </nav>

        <button
          type="button"
          className="md:hidden flex flex-col gap-1 p-2"
          onClick={() => setMobileOpen(!mobileOpen)}
          aria-label={t("header.toggleMenu")}
          aria-expanded={mobileOpen}
          aria-controls={mobileMenuId}
        >
          <span className={`block h-0.5 w-5 bg-foreground transition-all ${mobileOpen ? "rotate-45 translate-y-1.5" : ""}`} />
          <span className={`block h-0.5 w-5 bg-foreground transition-all ${mobileOpen ? "opacity-0" : ""}`} />
          <span className={`block h-0.5 w-5 bg-foreground transition-all ${mobileOpen ? "-rotate-45 -translate-y-1.5" : ""}`} />
        </button>
      </div>

      <AnimatePresence>
        {mobileOpen && (
          <motion.div
            id={mobileMenuId}
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.2, ease: "easeInOut" }}
            className="md:hidden border-t border-border/50 bg-background/95 backdrop-blur-xl overflow-hidden"
          >
            <div className="px-6 py-4 space-y-1">
              {NAV_LINKS.filter((link) => {
                if (link.adminRequired && !isAdmin) return false;
                if (link.authRequired && auth.authStatus !== "authenticated") return false;
                return true;
              }).map((link) => (
                <Link
                  key={link.href}
                  to={link.href}
                  onClick={() => setMobileOpen(false)}
                  className={`block rounded-lg px-3 py-2.5 text-sm font-medium transition-all ${
                    isActive(link)
                      ? "text-primary bg-primary/8"
                      : "text-muted-foreground hover:text-foreground hover:bg-foreground/5"
                  }`}
                >
                  {t(link.labelKey)}
                </Link>
              ))}
              <div className="divider-fade my-3" />

              <div className="space-y-3 py-2">
                <div className="flex items-center justify-between gap-3">
                  <span className="text-xs text-muted-foreground">{t("language.switcherLabel")}</span>
                  <LanguageSwitcher />
                </div>
                <div className="flex items-center justify-between gap-3">
                  <span className="text-xs text-muted-foreground">{t("theme.toggle")}</span>
                  <ThemeToggle />
                </div>
                <AuthButtonGroup mobile />
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </header>
  );
}
