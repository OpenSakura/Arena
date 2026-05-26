import type { ReactNode } from "react";

import i18next from "i18next";
import { I18nextProvider, initReactI18next } from "react-i18next";

import {
  DEFAULT_LOCALE,
  DEFAULT_NS,
  SUPPORTED_LOCALES,
  type UiLocale,
} from "./constants";
import { enResource } from "./resources/en";
import { zhResource } from "./resources/zh";

const testI18nResources = {
  en: {
    [DEFAULT_NS]: enResource,
  },
  zh: {
    [DEFAULT_NS]: zhResource,
  },
} as const;

export async function createTestI18n(locale: UiLocale = DEFAULT_LOCALE) {
  const instance = i18next.createInstance();

  await instance.use(initReactI18next).init({
    resources: testI18nResources,
    lng: locale,
    fallbackLng: DEFAULT_LOCALE,
    supportedLngs: [...SUPPORTED_LOCALES],
    ns: DEFAULT_NS,
    defaultNS: DEFAULT_NS,
    interpolation: {
      escapeValue: false,
    },
    react: {
      useSuspense: false,
    },
  });

  return instance;
}

export function TestI18nProvider({
  children,
  i18n,
  initialLanguage,
}: {
  children: ReactNode;
  i18n: Awaited<ReturnType<typeof createTestI18n>>;
  initialLanguage?: UiLocale;
}) {
  if (initialLanguage && i18n && i18n.language !== initialLanguage) {
    i18n.changeLanguage(initialLanguage);
  }
  return <I18nextProvider i18n={i18n}>{children}</I18nextProvider>;
}
