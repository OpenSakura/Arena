export const SUPPORTED_LOCALES = ["en", "zh"] as const;
export type UiLocale = (typeof SUPPORTED_LOCALES)[number];

export const DEFAULT_LOCALE: UiLocale = "en";
export const USER_LOCALE_STORAGE_KEY = "user-locale";
export const DEFAULT_NS = "translation";

export function normalizeUiLocale(value: unknown): UiLocale | null {
  if (typeof value !== "string") {
    return null;
  }

  const normalized = value.trim().toLowerCase();
  if (!normalized) {
    return null;
  }

  for (const locale of SUPPORTED_LOCALES) {
    if (normalized === locale) {
      return locale;
    }

    if (normalized.startsWith(`${locale}-`)) {
      const subtags = normalized.slice(locale.length + 1).split("-");
      return subtags.every((subtag) => /^[a-z0-9]+$/.test(subtag)) ? locale : null;
    }
  }

  return null;
}
