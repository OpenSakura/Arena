import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { LanguageSwitcher } from "./LanguageSwitcher";
import { createTestI18n, TestI18nProvider } from "@/i18n/test-utils";
import { USER_LOCALE_STORAGE_KEY } from "@/i18n/constants";

describe("LanguageSwitcher", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    localStorage.clear();
  });

  it("anonymous switch explicitly proves selecting 中文 calls changeLanguage, persists storage, and exposes accessible label", async () => {
    const i18n = await createTestI18n("en");
    render(
      <TestI18nProvider i18n={i18n}>
        <LanguageSwitcher />
      </TestI18nProvider>
    );

    const select = screen.getByRole("combobox") as HTMLSelectElement;
    expect(select).not.toBeNull();
    expect(select.value).toBe("en");
    
    // Exposed accessible label
    expect(select.getAttribute("aria-label")).toBe("Language / 语言");

    fireEvent.change(select, { target: { value: "zh" } });

    await waitFor(() => {
      expect(i18n.resolvedLanguage).toBe("zh");
    });
    
    expect(localStorage.getItem(USER_LOCALE_STORAGE_KEY)).toBe("zh");
    expect(select.value).toBe("zh");
  });
});
