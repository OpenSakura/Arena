import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { useMatches } from "react-router-dom";

export function I18nDocumentEffects() {
  const { t, i18n } = useTranslation();
  const matches = useMatches();

  useEffect(() => {
    // 1. Update HTML lang attribute
    document.documentElement.lang = i18n.resolvedLanguage || "en";
    
    // 2. Update HTML dir attribute safely
    const currentLang = i18n.resolvedLanguage || "en";
    document.documentElement.dir = typeof i18n.dir === "function" ? i18n.dir(currentLang) : "ltr";

    // 3. Update document title
    const appTitle = t("app.title", "OpenSakura Arena");
    let routeTitleKey = null;

    // Find the deepest match with handle.titleKey
    for (let i = matches.length - 1; i >= 0; i--) {
      const handle = matches[i].handle as { titleKey?: string } | undefined;
      if (handle?.titleKey) {
        routeTitleKey = handle.titleKey;
        break;
      }
    }

    if (routeTitleKey) {
      document.title = `${t(routeTitleKey)} | ${appTitle}`;
    } else {
      document.title = appTitle;
    }
  }, [i18n.resolvedLanguage, i18n, matches, t]);

  return null;
}
