import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { useTheme } from "@/components/ThemeProvider";

function SunIcon({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2" />
      <path d="M12 20v2" />
      <path d="M4.93 4.93l1.41 1.41" />
      <path d="M17.66 17.66l1.41 1.41" />
      <path d="M2 12h2" />
      <path d="M20 12h2" />
      <path d="M4.93 19.07l1.41-1.41" />
      <path d="M17.66 6.34l1.41-1.41" />
    </svg>
  );
}

function MoonIcon({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden>
      <path d="M21 12.5A7.5 7.5 0 1 1 11.5 3a6 6 0 0 0 9.5 9.5Z" />
    </svg>
  );
}

export function ThemeToggle({ className = "" }: { className?: string }) {
  const { theme, toggleTheme, mounted } = useTheme();
  const { t } = useTranslation();
  const isDark = theme === "dark";

  const label = !mounted
    ? t("theme.toggle")
    : isDark
      ? t("theme.switchToLight")
      : t("theme.switchToDark");

  return (
    <Button
      type="button"
      variant="outline"
      size="icon"
      onClick={toggleTheme}
      aria-label={label}
      aria-pressed={mounted ? isDark : undefined}
      title={label}
      className={`h-7 w-7 rounded-full border-border/50 bg-background/30 hover:bg-accent/60 ${className}`}
    >
      <span className="relative block h-4 w-4">
        <SunIcon
          className={`absolute inset-0 transition-opacity ${!mounted ? "opacity-0 dark:opacity-100" : isDark ? "opacity-100" : "opacity-0"}`}
        />
        <MoonIcon
          className={`absolute inset-0 transition-opacity ${!mounted ? "opacity-100 dark:opacity-0" : !isDark ? "opacity-100" : "opacity-0"}`}
        />
      </span>
    </Button>
  );
}
