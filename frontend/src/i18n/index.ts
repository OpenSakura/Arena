import i18n from "i18next";
import LanguageDetector from "i18next-browser-languagedetector";
import { initReactI18next } from "react-i18next";

import {
  DEFAULT_LOCALE,
  DEFAULT_NS,
  SUPPORTED_LOCALES,
  USER_LOCALE_STORAGE_KEY,
} from "./constants";
import { enResource } from "./resources/en";
import { zhResource } from "./resources/zh";

export const i18nResources = {
  en: {
    [DEFAULT_NS]: enResource,
  },
  zh: {
    [DEFAULT_NS]: zhResource,
  },
} as const;

export const i18nInitPromise = i18n.use(LanguageDetector).use(initReactI18next).init({
  resources: i18nResources,
  fallbackLng: DEFAULT_LOCALE,
  supportedLngs: [...SUPPORTED_LOCALES],
  nonExplicitSupportedLngs: true,
  load: "languageOnly",
  detection: {
    order: ["localStorage", "navigator", "htmlTag"],
    lookupLocalStorage: USER_LOCALE_STORAGE_KEY,
    caches: [],
    excludeCacheFor: ["cimode"],
  },
  ns: DEFAULT_NS,
  defaultNS: DEFAULT_NS,
  interpolation: {
    escapeValue: false,
  },
  react: {
    useSuspense: false,
  },
});

export default i18n;
export { normalizeUiLocale } from "./constants";
