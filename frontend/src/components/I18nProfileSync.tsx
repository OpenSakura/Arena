import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { useArenaAuth } from "@/hooks/useArenaAuth";
import { apiGet, apiPut } from "@/lib/api";
import { parseMeResponse, type MeProfile } from "@/types/me";
import { normalizeUiLocale, USER_LOCALE_STORAGE_KEY } from "@/i18n/constants";

export function I18nProfileSync() {
  const { authStatus } = useArenaAuth();
  const { i18n } = useTranslation();
  const profileRef = useRef<{ loaded: boolean; profile: MeProfile | null }>({ loaded: false, profile: null });

  useEffect(() => {
    if (authStatus !== "authenticated") {
      profileRef.current = { loaded: false, profile: null };
      return;
    }

    let active = true;

    async function fetchMe() {
      try {
        const data = await apiGet("/me");
        if (!active) return;
        
        const response = parseMeResponse(data);
        profileRef.current = { loaded: true, profile: response.profile ?? null };

        const storedLocale = window.localStorage.getItem(USER_LOCALE_STORAGE_KEY);
        if (!storedLocale && response.profile?.ui_language) {
          const profileLocale = normalizeUiLocale(response.profile.ui_language);
          if (profileLocale) {
            await i18n.changeLanguage(profileLocale);
            window.localStorage.setItem(USER_LOCALE_STORAGE_KEY, profileLocale);
          }
        }
      } catch (_error: unknown) {
        // ignore fetch failures as this is non-blocking
        void _error;
      }
    }

    void fetchMe();

    return () => {
      active = false;
    };
  }, [authStatus, i18n]);

  useEffect(() => {
    const handleLocaleChanged = (event: Event) => {
      const customEvent = event as CustomEvent<{ locale: string }>;
      const newLocale = customEvent.detail.locale;

      if (authStatus === "authenticated") {
        const normalizedLocale = normalizeUiLocale(newLocale);
        if (!normalizedLocale) return;

        const updateProfile = async () => {
          let currentData = profileRef.current;
          if (!currentData.loaded) {
            try {
              const data = await apiGet("/me");
              const response = parseMeResponse(data);
              currentData = { loaded: true, profile: response.profile ?? null };
              profileRef.current = currentData;
            } catch (_error: unknown) {
              void _error;
              return;
            }
          }

          if (currentData.profile !== null) {
            const p = currentData.profile;
            const payload = {
              display_name: p.display_name ?? null,
              ui_language: normalizedLocale,
              zh_variant: p.zh_variant ?? null,
              jp_proficiency: p.jp_proficiency ?? null,
              translation_experience: p.translation_experience ?? null,
              consents: p.consents ?? null,
            };

            // Fire and forget, ignore failures
            apiPut("/me/profile", payload).catch(function ignoreNonBlockingFailure(_error: unknown) {
              void _error;
            });
          }
        };
        void updateProfile();
        
      }
    };

    window.addEventListener("arena-i18n-locale-changed", handleLocaleChanged);
    return () => {
      window.removeEventListener("arena-i18n-locale-changed", handleLocaleChanged);
    };
  }, [authStatus]);

  return null;
}
