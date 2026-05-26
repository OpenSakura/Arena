// @vitest-environment jsdom

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi, beforeAll } from "vitest";

import { createTestI18n, TestI18nProvider } from "@/i18n/test-utils";
import OnboardingRoute from "./OnboardingRoute";

const useAuthHeadersMock = vi.fn();
const apiGetMock = vi.fn();
const apiPutMock = vi.fn();

vi.mock("@/hooks/useAuthHeaders", () => ({
  useAuthHeaders: () => useAuthHeadersMock(),
}));

vi.mock("@/lib/api", () => ({
  apiGet: (...args: unknown[]) => apiGetMock(...args),
  apiPut: (...args: unknown[]) => apiPutMock(...args),
}));

function authenticatedSession() {
  useAuthHeadersMock.mockReturnValue({
    authStatus: "authenticated",
    csrfToken: "csrf-token",
    sessionError: null,
  });
}

beforeEach(() => {
  useAuthHeadersMock.mockReset();
  apiGetMock.mockReset();
  apiPutMock.mockReset();

  useAuthHeadersMock.mockReturnValue({
    authStatus: "unauthenticated",
    sessionError: null,
    csrfToken: null,
  });
});

describe("OnboardingRoute", () => {
  let i18nEn: Awaited<ReturnType<typeof createTestI18n>>;
  let i18nZh: Awaited<ReturnType<typeof createTestI18n>>;

  beforeAll(async () => {
    i18nEn = await createTestI18n("en");
    i18nZh = await createTestI18n("zh");
  });

  it("shows login guard and disables profile save for anonymous users", async () => {
    render(
      <TestI18nProvider i18n={i18nEn}>
        <OnboardingRoute />
      </TestI18nProvider>
    );

    await screen.findByText("Login required to save");
    expect(apiGetMock).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Save profile" }).hasAttribute("disabled")).toBe(true);
  });

  it("shows explicit re-login copy for expired sessions", async () => {
    useAuthHeadersMock.mockReturnValue({
      authStatus: "authenticated",
      csrfToken: "csrf-token",
      sessionError: "SessionExpired",
    });

    render(
      <TestI18nProvider i18n={i18nEn}>
        <OnboardingRoute />
      </TestI18nProvider>
    );

    await screen.findByText("Session expired");
    expect(
      screen.getByText(/Your session expired before we could load or save your profile/i),
    ).toBeDefined();
    expect(apiGetMock).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Save profile" }).hasAttribute("disabled")).toBe(true);
  });

  it("loads profile fields using the backend session", async () => {
    authenticatedSession();
    apiGetMock.mockResolvedValue({
      authenticated: true,
      profile: {
        display_name: "Aki",
        ui_language: "ja",
        zh_variant: "zh-Hant",
        jp_proficiency: { jlpt: "N2" },
        translation_experience: {
          jp_zh: {
            years: "3-5",
            roles: ["translator", "ignored-role"],
          },
        },
        consents: { research_use: true },
        completed_at: "2026-02-19T10:00:00Z",
      },
    });

    render(
      <TestI18nProvider i18n={i18nEn}>
        <OnboardingRoute />
      </TestI18nProvider>
    );

    await waitFor(() => {
      expect(apiGetMock).toHaveBeenCalledWith("/me");
    });
    expect(apiGetMock.mock.calls[0]).toHaveLength(1);

    const displayName = screen.getByLabelText("Display name (optional)") as HTMLInputElement;
    const uiLanguage = screen.getByLabelText("UI language") as HTMLSelectElement;
    const zhVariant = screen.getByLabelText("Chinese variant") as HTMLSelectElement;
    const jlpt = screen.getByLabelText("Japanese proficiency (self-reported)") as HTMLSelectElement;
    const experienceYears = screen.getByLabelText("JP->ZH experience (years)") as HTMLSelectElement;

    await waitFor(() => {
      expect(displayName.value).toBe("Aki");
      expect(uiLanguage.value).toBe("ja");
      expect(zhVariant.value).toBe("zh-Hant");
      expect(jlpt.value).toBe("N2");
      expect(experienceYears.value).toBe("3-5");

      const translatorRole = screen.getByRole("button", { name: /translator/i });
      expect(translatorRole.className).toContain("primary");
      expect(screen.queryByText("Saved successfully")).toBeNull();
    });
  });

  it("submits null-valued optional fields when profile stays at defaults", async () => {
    authenticatedSession();
    apiGetMock.mockResolvedValue({ authenticated: true, profile: null });
    apiPutMock.mockResolvedValue({ authenticated: true, profile: {} });

    render(
      <TestI18nProvider i18n={i18nEn}>
        <OnboardingRoute />
      </TestI18nProvider>
    );

    await waitFor(() => {
      expect(apiGetMock).toHaveBeenCalledTimes(1);
    });

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Save profile" }));

    await waitFor(() => {
      expect(apiPutMock).toHaveBeenCalledWith(
        "/me/profile",
        {
          display_name: null,
          ui_language: "en",
          zh_variant: "zh-Hans",
          jp_proficiency: null,
          translation_experience: null,
          consents: { research_use: false },
        },
      );
    });
    expect(apiPutMock.mock.calls[0]).toHaveLength(2);

    await screen.findByText("Saved successfully");
  });

  it("submits normalized profile payload for non-default user input", async () => {
    authenticatedSession();
    apiGetMock.mockResolvedValue({ authenticated: true, profile: null });
    apiPutMock.mockResolvedValue({
      authenticated: true,
      profile: {
        completed_at: "2026-02-19T11:11:11Z",
      },
    });

    render(
      <TestI18nProvider i18n={i18nEn}>
        <OnboardingRoute />
      </TestI18nProvider>
    );

    await waitFor(() => {
      expect(apiGetMock).toHaveBeenCalledTimes(1);
    });

    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Display name (optional)"), "  Veteran Reviewer  ");
    await user.selectOptions(screen.getByLabelText("UI language"), "zh");
    await user.selectOptions(screen.getByLabelText("Chinese variant"), "zh-Hant");
    await user.selectOptions(screen.getByLabelText("Japanese proficiency (self-reported)"), "N1");
    await user.selectOptions(screen.getByLabelText("JP->ZH experience (years)"), "1-3");
    await user.click(screen.getByRole("button", { name: /editor/i }));
    await user.click(
      screen.getByRole("checkbox", {
        name: "Allow using my profile answers for offline filtering/research.",
      }),
    );
    await user.click(screen.getByRole("button", { name: "Save profile" }));

    await waitFor(() => {
      expect(apiPutMock).toHaveBeenCalledWith(
        "/me/profile",
        {
          display_name: "Veteran Reviewer",
          ui_language: "zh",
          zh_variant: "zh-Hant",
          jp_proficiency: { jlpt: "N1" },
          translation_experience: {
            jp_zh: {
              years: "1-3",
              roles: ["editor"],
            },
          },
          consents: { research_use: true },
        },
      );
    });
    expect(apiPutMock.mock.calls[0]).toHaveLength(2);

    await screen.findByText("Saved successfully");
  });

  it("shows API errors when profile load or save fails", async () => {
    authenticatedSession();
    apiGetMock.mockRejectedValueOnce(new Error("load failed"));
    apiPutMock.mockRejectedValueOnce(new Error("save failed"));

    render(
      <TestI18nProvider i18n={i18nEn}>
        <OnboardingRoute />
      </TestI18nProvider>
    );

    await screen.findByText("load failed");

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Save profile" }));

    await screen.findByText("save failed");
  });

  it("renders translated strings and exact option values using Chinese variant", async () => {
    authenticatedSession();
    apiGetMock.mockResolvedValue({ authenticated: true, profile: null });
    apiPutMock.mockResolvedValue({ authenticated: true, profile: {} });

    render(
      <TestI18nProvider i18n={i18nZh}>
        <OnboardingRoute />
      </TestI18nProvider>
    );

    await screen.findByText("个人资料");
    expect(screen.getByLabelText("昵称（可选）")).toBeDefined();
    expect(screen.getByRole("button", { name: "保存资料" })).toBeDefined();

    const zhVariantSelect = screen.getByLabelText("中文变体") as HTMLSelectElement;
    expect(zhVariantSelect).toBeDefined();
    
    const options = Array.from(zhVariantSelect.options);
    expect(options.some(opt => opt.value === "zh-Hans")).toBe(true);
    expect(options.some(opt => opt.value === "zh-Hant")).toBe(true);
    expect(options.some(opt => opt.text.includes("简体"))).toBe(true);
    expect(options.some(opt => opt.text.includes("繁体"))).toBe(true);
  });

  it("shows explicit re-login copy for expired sessions in Chinese", async () => {
    useAuthHeadersMock.mockReturnValue({
      authStatus: "authenticated",
      csrfToken: "csrf-token",
      sessionError: "SessionExpired",
    });

    render(
      <TestI18nProvider i18n={i18nZh}>
        <OnboardingRoute />
      </TestI18nProvider>
    );

    await screen.findByText("登录已过期");
    expect(
      screen.getByText(/加载或保存资料前登录已过期/i),
    ).toBeDefined();
    expect(apiGetMock).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "保存资料" }).hasAttribute("disabled")).toBe(true);
  });

  it("submits payload preserving exact domain values under Chinese locale", async () => {
    authenticatedSession();
    apiGetMock.mockResolvedValue({ authenticated: true, profile: null });
    apiPutMock.mockResolvedValue({ authenticated: true, profile: {} });

    render(
      <TestI18nProvider i18n={i18nZh}>
        <OnboardingRoute />
      </TestI18nProvider>
    );

    await waitFor(() => {
      expect(apiGetMock).toHaveBeenCalledTimes(1);
    });

    const user = userEvent.setup();
    await user.selectOptions(screen.getByLabelText("中文变体"), "zh-Hant");
    await user.click(screen.getByRole("button", { name: "保存资料" }));

    await waitFor(() => {
      expect(apiPutMock).toHaveBeenCalledWith(
        "/me/profile",
        expect.objectContaining({
          zh_variant: "zh-Hant",
        }),
      );
    });
    expect(apiPutMock.mock.calls[0]).toHaveLength(2);

    await screen.findByText("保存成功");
  });
});
