// @vitest-environment jsdom

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import OnboardingPage from "./page";

const useSessionMock = vi.fn();
const apiGetMock = vi.fn();
const apiPutMock = vi.fn();

vi.mock("next-auth/react", () => ({
  useSession: () => useSessionMock(),
}));

vi.mock("@/lib/api", () => ({
  apiGet: (...args: unknown[]) => apiGetMock(...args),
  apiPut: (...args: unknown[]) => apiPutMock(...args),
}));

function authenticatedSession() {
  useSessionMock.mockReturnValue({
    data: { accessToken: "access-token" },
    status: "authenticated",
  });
}

beforeEach(() => {
  useSessionMock.mockReset();
  apiGetMock.mockReset();
  apiPutMock.mockReset();
});

describe("OnboardingPage", () => {
  it("shows login guard and disables profile save for anonymous users", async () => {
    useSessionMock.mockReturnValue({ data: null, status: "unauthenticated" });

    render(<OnboardingPage />);

    await screen.findByText("Login required to save");
    expect(apiGetMock).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Save profile" }).hasAttribute("disabled")).toBe(true);
  });

  it("shows explicit re-login copy for expired sessions", async () => {
    useSessionMock.mockReturnValue({
      data: { accessToken: "access-token", error: "RefreshTokenExpired" },
      status: "authenticated",
    });

    render(<OnboardingPage />);

    await screen.findByText("Session expired");
    expect(
      screen.getByText(/Your session expired before we could load or save your profile/i),
    ).toBeDefined();
    expect(apiGetMock).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Save profile" }).hasAttribute("disabled")).toBe(true);
  });

  it("loads profile fields using authenticated access token", async () => {
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

    render(<OnboardingPage />);

    await waitFor(() => {
      expect(apiGetMock).toHaveBeenCalledWith("/me", {
        headers: { Authorization: "Bearer access-token" },
      });
    });

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

    render(<OnboardingPage />);

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
        {
          headers: { Authorization: "Bearer access-token" },
        },
      );
    });

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

    render(<OnboardingPage />);

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
        {
          headers: { Authorization: "Bearer access-token" },
        },
      );
    });

    await screen.findByText("Saved successfully");
  });

  it("shows API errors when profile load or save fails", async () => {
    authenticatedSession();
    apiGetMock.mockRejectedValueOnce(new Error("load failed"));
    apiPutMock.mockRejectedValueOnce(new Error("save failed"));

    render(<OnboardingPage />);

    await screen.findByText("load failed");

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Save profile" }));

    await screen.findByText("save failed");
  });
});
