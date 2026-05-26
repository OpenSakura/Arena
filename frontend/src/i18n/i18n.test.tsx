// @vitest-environment jsdom

import i18next from "i18next";
import LanguageDetector from "i18next-browser-languagedetector";
import { initReactI18next } from "react-i18next";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  DEFAULT_LOCALE,
  DEFAULT_NS,
  SUPPORTED_LOCALES,
  USER_LOCALE_STORAGE_KEY,
  normalizeUiLocale,
} from "./constants";
import i18n, { i18nInitPromise, i18nResources } from "./index";
import { enResource } from "./resources/en";
import { zhResource } from "./resources/zh";
import { createTestI18n } from "./test-utils";

type CatalogNode = string | { readonly [key: string]: CatalogNode };

const REQUIRED_TOP_LEVEL_GROUPS = [
  "app",
  "language",
  "auth",
  "nav",
  "header",
  "theme",
  "common",
  "errors",
  "routes",
  "home",
  "footer",
  "leaderboard",
  "battle",
  "onboarding",
  "admin",
] as const;

function flattenKeyPaths(node: CatalogNode, prefix = ""): string[] {
  if (typeof node === "string") {
    return [prefix];
  }

  return Object.entries(node).flatMap(([key, value]) => {
    const nextPrefix = prefix ? `${prefix}.${key}` : key;
    return flattenKeyPaths(value, nextPrefix);
  });
}

function setNavigatorLanguage(language: string) {
  Object.defineProperty(window.navigator, "language", {
    configurable: true,
    value: language,
  });
  Object.defineProperty(window.navigator, "languages", {
    configurable: true,
    value: [language],
  });
}

async function createDetectedInstance() {
  const instance = i18next.createInstance();

  await instance.use(LanguageDetector).use(initReactI18next).init({
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

  return instance;
}

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
  document.documentElement.removeAttribute("lang");
  setNavigatorLanguage("en-US");
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("i18n resources", () => {
  it("supports exactly English and Chinese locales", () => {
    expect(SUPPORTED_LOCALES).toEqual(["en", "zh"]);
    expect(DEFAULT_LOCALE).toBe("en");
    expect(USER_LOCALE_STORAGE_KEY).toBe("user-locale");
    expect(DEFAULT_NS).toBe("translation");
    expect(Object.keys(i18nResources).sort()).toEqual(["en", "zh"]);
  });

  it("keeps starter catalog key trees in parity", () => {
    expect(Object.keys(enResource).sort()).toEqual([...REQUIRED_TOP_LEVEL_GROUPS].sort());
    expect(Object.keys(zhResource).sort()).toEqual([...REQUIRED_TOP_LEVEL_GROUPS].sort());

    const enKeys = flattenKeyPaths(enResource).sort();
    const zhKeys = flattenKeyPaths(zhResource).sort();

    expect(enKeys.length).toBeGreaterThan(REQUIRED_TOP_LEVEL_GROUPS.length);
    expect(zhKeys).toEqual(enKeys);
  });
});

describe("normalizeUiLocale", () => {
  it("maps supported base and regional language tags", () => {
    expect(normalizeUiLocale("en")).toBe("en");
    expect(normalizeUiLocale("en-US")).toBe("en");
    expect(normalizeUiLocale("en-gb")).toBe("en");
    expect(normalizeUiLocale("zh")).toBe("zh");
    expect(normalizeUiLocale("zh-CN")).toBe("zh");
    expect(normalizeUiLocale("zh-Hant")).toBe("zh");
  });

  it("rejects unsupported or corrupt values", () => {
    expect(normalizeUiLocale("ja")).toBeNull();
    expect(normalizeUiLocale("")).toBeNull();
    expect(normalizeUiLocale("en-")).toBeNull();
    expect(normalizeUiLocale("zh--CN")).toBeNull();
    expect(normalizeUiLocale(null)).toBeNull();
    expect(normalizeUiLocale(42)).toBeNull();
    expect(normalizeUiLocale({ locale: "zh" })).toBeNull();
  });
});

describe("i18next runtime", () => {
  it("uses persisted zh locale", async () => {
    window.localStorage.setItem(USER_LOCALE_STORAGE_KEY, "zh");

    const instance = await createDetectedInstance();

    expect(instance.resolvedLanguage).toBe("zh");
    expect(instance.t("language.current")).toBe(zhResource.language.current);
  });

  it("falls back to en", async () => {
    window.localStorage.setItem(USER_LOCALE_STORAGE_KEY, "fr-FR");
    setNavigatorLanguage("fr-FR");

    const instance = await createDetectedInstance();

    expect(instance.resolvedLanguage).toBe("en");
    expect(instance.t("language.current")).toBe(enResource.language.current);
  });

  it("normalizes regional Chinese browser locales to zh", async () => {
    setNavigatorLanguage("zh-CN");
    let instance = await createDetectedInstance();
    expect(instance.resolvedLanguage).toBe("zh");

    setNavigatorLanguage("zh-Hant");
    instance = await createDetectedInstance();
    expect(instance.resolvedLanguage).toBe("zh");
  });

  it("reads html lang after localStorage and navigator", async () => {
    setNavigatorLanguage("fr-FR");
    document.documentElement.lang = "zh-Hant";

    const instance = await createDetectedInstance();

    expect(instance.resolvedLanguage).toBe("zh");
  });

  it("does not auto-cache detector choices", async () => {
    setNavigatorLanguage("zh-CN");
    const setItemSpy = vi.spyOn(window.localStorage.__proto__, "setItem");

    const instance = await createDetectedInstance();
    await instance.changeLanguage("en");

    expect(instance.resolvedLanguage).toBe("en");
    expect(window.localStorage.getItem(USER_LOCALE_STORAGE_KEY)).toBeNull();
    expect(
      setItemSpy.mock.calls.some(([key]) => key === USER_LOCALE_STORAGE_KEY),
    ).toBe(false);
  });

  it("exposes an initialized app singleton without explicit production lng", async () => {
    await i18nInitPromise;

    expect(i18n.options.lng).toBeUndefined();
    expect(i18n.options.fallbackLng).toEqual([DEFAULT_LOCALE]);
    expect(i18n.options.supportedLngs).toEqual(["en", "zh", "cimode"]);
    expect(i18n.options.load).toBe("languageOnly");
    expect(i18n.options.detection).toMatchObject({
      order: ["localStorage", "navigator", "htmlTag"],
      lookupLocalStorage: USER_LOCALE_STORAGE_KEY,
      caches: [],
      excludeCacheFor: ["cimode"],
    });
  });
});

describe("createTestI18n", () => {
  it("creates isolated test i18n instances", async () => {
    const english = await createTestI18n("en");
    const chinese = await createTestI18n("zh");

    expect(english).not.toBe(chinese);
    expect(english.resolvedLanguage).toBe("en");
    expect(chinese.resolvedLanguage).toBe("zh");
    expect(english.t("language.current")).toBe(enResource.language.current);
    expect(chinese.t("language.current")).toBe(zhResource.language.current);

    await english.changeLanguage("zh");

    expect(english.resolvedLanguage).toBe("zh");
    expect(chinese.resolvedLanguage).toBe("zh");
  });
});
