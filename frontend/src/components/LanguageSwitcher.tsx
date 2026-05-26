import { useTranslation } from "react-i18next";
import { useId } from "react";
import { USER_LOCALE_STORAGE_KEY } from "@/i18n/constants";

export function LanguageSwitcher({ className = "" }: { className?: string }) {
  const { t, i18n } = useTranslation();
  const selectId = useId();

  const handleLanguageChange = async (event: React.ChangeEvent<HTMLSelectElement>) => {
    const newLocale = event.target.value;
    await i18n.changeLanguage(newLocale);
    try {
      if (typeof window !== "undefined") {
        window.localStorage.setItem(USER_LOCALE_STORAGE_KEY, newLocale);
        // Dispatch a custom event so I18nProfileSync can listen to it.
        window.dispatchEvent(new CustomEvent("arena-i18n-locale-changed", { detail: { locale: newLocale } }));
      }
    } catch (error) {
      void error;
    }
  };

  return (
    <div className={`flex items-center gap-2 ${className}`}>
      <label htmlFor={selectId} className="sr-only">
        {t("language.switcherLabel")}
      </label>
      <select
        id={selectId}
        value={i18n.resolvedLanguage || "en"}
        onChange={(e) => void handleLanguageChange(e)}
        className="h-7 rounded-md border border-border/50 bg-background/30 px-2 py-0 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-ring hover:bg-accent/60 cursor-pointer"
        aria-label={t("language.switcherLabel")}
      >
        <option value="en">English</option>
        <option value="zh">中文</option>
      </select>
    </div>
  );
}
