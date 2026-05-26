import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, waitFor, act } from "@testing-library/react";
import { I18nProfileSync } from "./I18nProfileSync";
import { createTestI18n, TestI18nProvider } from "@/i18n/test-utils";
import * as api from "@/lib/api";
import * as auth from "@/hooks/useArenaAuth";
import type { ArenaAuthContextValue } from "@/auth/ArenaAuthProvider";
import type { MeResponse } from "@/types/me";
import { USER_LOCALE_STORAGE_KEY } from "@/i18n/constants";

vi.mock("@/lib/api", () => ({
  apiGet: vi.fn(),
  apiPut: vi.fn(),
}));

vi.mock("@/hooks/useArenaAuth", () => ({
  useArenaAuth: vi.fn(),
}));

function mockAuthStatus(status: ArenaAuthContextValue["authStatus"]) {
  vi.mocked(auth.useArenaAuth).mockReturnValue({
    authStatus: status,
    isLoading: false,
    isAuthenticated: status === "authenticated",
    user: null,
    csrfToken: null,
    sessionError: null,
    signinRedirect: vi.fn(),
    signoutRedirect: vi.fn(),
  });
}

describe("I18nProfileSync", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.clearAllMocks();
  });

  afterEach(() => {
    localStorage.clear();
  });

  it("applies profile ui_language when localStorage is absent", async () => {
    const i18n = await createTestI18n("en");
    mockAuthStatus("authenticated");
    vi.mocked(api.apiGet).mockResolvedValue({
      authenticated: true,
      profile: { ui_language: "zh" }
    });

    render(
      <TestI18nProvider i18n={i18n}>
        <I18nProfileSync />
      </TestI18nProvider>
    );

    await waitFor(() => {
      expect(i18n.resolvedLanguage).toBe("zh");
      expect(localStorage.getItem(USER_LOCALE_STORAGE_KEY)).toBe("zh");
    });
  });

  it("does not apply profile ui_language when localStorage is present", async () => {
    localStorage.setItem(USER_LOCALE_STORAGE_KEY, "en");
    const i18n = await createTestI18n("zh");
    mockAuthStatus("authenticated");
    vi.mocked(api.apiGet).mockResolvedValue({
      authenticated: true,
      profile: { ui_language: "zh" }
    });

    render(
      <TestI18nProvider i18n={i18n}>
        <I18nProfileSync />
      </TestI18nProvider>
    );

    await waitFor(() => {
      expect(api.apiGet).toHaveBeenCalled();
    });

    expect(localStorage.getItem(USER_LOCALE_STORAGE_KEY)).toBe("en");
  });

  it("profile beats browser", async () => {
    // absent localStorage + browser/runtime starting in `zh`
    // authenticated profile ui_language: "en" forces final locale/storage to "en"
    const i18n = await createTestI18n("zh");
    expect(localStorage.getItem(USER_LOCALE_STORAGE_KEY)).toBeNull();

    mockAuthStatus("authenticated");
    vi.mocked(api.apiGet).mockResolvedValue({
      authenticated: true,
      profile: { ui_language: "en" }
    });

    render(
      <TestI18nProvider i18n={i18n}>
        <I18nProfileSync />
      </TestI18nProvider>
    );

    await waitFor(() => {
      expect(i18n.resolvedLanguage).toBe("en");
      expect(localStorage.getItem(USER_LOCALE_STORAGE_KEY)).toBe("en");
    });
  });

  it("treats unsupported profile values as no-op", async () => {
    const i18n = await createTestI18n("en");
    mockAuthStatus("authenticated");
    vi.mocked(api.apiGet).mockResolvedValue({
      authenticated: true,
      profile: { ui_language: "fr" } // Unsupported
    });

    render(
      <TestI18nProvider i18n={i18n}>
        <I18nProfileSync />
      </TestI18nProvider>
    );

    await waitFor(() => {
      expect(api.apiGet).toHaveBeenCalled();
    });

    expect(i18n.resolvedLanguage).toBe("en");
    expect(localStorage.getItem(USER_LOCALE_STORAGE_KEY)).toBeNull();
  });

  it("explicit switch with non-null profile attempts PUT /me/profile", async () => {
    const i18n = await createTestI18n("en");
    mockAuthStatus("authenticated");
    vi.mocked(api.apiGet).mockResolvedValue({
      authenticated: true,
      profile: { display_name: "Test", ui_language: "en" }
    });
    vi.mocked(api.apiPut).mockResolvedValue({});

    render(
      <TestI18nProvider i18n={i18n}>
        <I18nProfileSync />
      </TestI18nProvider>
    );

    await waitFor(() => {
      expect(api.apiGet).toHaveBeenCalled();
    });

    act(() => {
      window.dispatchEvent(new CustomEvent("arena-i18n-locale-changed", { detail: { locale: "zh" } }));
    });

    await waitFor(() => {
      expect(api.apiPut).toHaveBeenCalledWith("/me/profile", {
        display_name: "Test",
        ui_language: "zh",
        zh_variant: null,
        jp_proficiency: null,
        translation_experience: null,
        consents: null
      });
    });
  });

  it("explicit switch with profile not loaded yet fetches /me and attempts PUT /me/profile", async () => {
    const i18n = await createTestI18n("en");
    mockAuthStatus("authenticated");
    
    // First call happens after render, but we fire the event before it finishes
    let getResolve: (value: MeResponse) => void;
    const getPromise = new Promise((resolve) => {
      getResolve = resolve;
    });
    
    vi.mocked(api.apiGet).mockReturnValue(getPromise);
    vi.mocked(api.apiPut).mockResolvedValue({});

    render(
      <TestI18nProvider i18n={i18n}>
        <I18nProfileSync />
      </TestI18nProvider>
    );

    // Profile sync hasn't resolved apiGet yet
    act(() => {
      window.dispatchEvent(new CustomEvent("arena-i18n-locale-changed", { detail: { locale: "zh" } }));
    });

    // The explicit switch handler should now wait for its own apiGet
    act(() => {
      // Resolve it now
      getResolve!({
        authenticated: true,
        profile: { display_name: "Test", ui_language: "en" }
      });
    });

    await waitFor(() => {
      expect(api.apiGet).toHaveBeenCalled();
      expect(api.apiPut).toHaveBeenCalledWith("/me/profile", {
        display_name: "Test",
        ui_language: "zh",
        zh_variant: null,
        jp_proficiency: null,
        translation_experience: null,
        consents: null
      });
    });
  });

  it("profile null", async () => {
    // explicit switch with profile: null does not attempt PUT /me/profile but preserves localStorage
    const i18n = await createTestI18n("en");
    mockAuthStatus("authenticated");
    vi.mocked(api.apiGet).mockResolvedValue({
      authenticated: true,
      profile: null
    });

    render(
      <TestI18nProvider i18n={i18n}>
        <I18nProfileSync />
      </TestI18nProvider>
    );

    await waitFor(() => {
      expect(api.apiGet).toHaveBeenCalled();
    });

    act(() => {
      // Simulate LanguageSwitcher behavior
      i18n.changeLanguage("zh");
      localStorage.setItem(USER_LOCALE_STORAGE_KEY, "zh");
      window.dispatchEvent(new CustomEvent("arena-i18n-locale-changed", { detail: { locale: "zh" } }));
    });

    // Wait a tick to ensure no PUT call
    await new Promise(r => setTimeout(r, 50));
    expect(api.apiPut).not.toHaveBeenCalled();
    
    // Check that we maintained the language change
    expect(i18n.resolvedLanguage).toBe("zh");
    expect(localStorage.getItem(USER_LOCALE_STORAGE_KEY)).toBe("zh");
  });

  it("profile update failure", async () => {
    // local i18n locale and localStorage should remain `zh` after the PUT rejects, and no blocking UI/error
    const i18n = await createTestI18n("en");
    mockAuthStatus("authenticated");
    vi.mocked(api.apiGet).mockResolvedValue({
      authenticated: true,
      profile: { ui_language: "en" }
    });
    vi.mocked(api.apiPut).mockRejectedValue(new Error("Network error"));

    render(
      <TestI18nProvider i18n={i18n}>
        <I18nProfileSync />
      </TestI18nProvider>
    );

    await waitFor(() => {
      expect(api.apiGet).toHaveBeenCalled();
    });

    act(() => {
      // Simulate LanguageSwitcher behavior
      i18n.changeLanguage("zh");
      localStorage.setItem(USER_LOCALE_STORAGE_KEY, "zh");
      window.dispatchEvent(new CustomEvent("arena-i18n-locale-changed", { detail: { locale: "zh" } }));
    });

    await waitFor(() => {
      expect(api.apiPut).toHaveBeenCalled();
    });

    // We just ensure it handled the rejection cleanly.
    expect(i18n.resolvedLanguage).toBe("zh");
    expect(localStorage.getItem(USER_LOCALE_STORAGE_KEY)).toBe("zh");
  });
});
