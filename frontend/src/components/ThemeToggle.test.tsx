import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { createTestI18n, TestI18nProvider } from "@/i18n/test-utils";
import { ThemeProvider } from "./ThemeProvider";
import { ThemeToggle } from "./ThemeToggle";

describe("ThemeToggle", () => {
  it("renders and toggles theme", async () => {
    const i18nInstance = await createTestI18n("en");
    const user = userEvent.setup();
    render(
      <TestI18nProvider i18n={i18nInstance}>
        <ThemeProvider>
          <ThemeToggle />
        </ThemeProvider>
      </TestI18nProvider>
    );

    const button = screen.getByRole("button");
    expect(button).toBeDefined();
    
    const initialLabel = button.getAttribute("aria-label");
    await user.click(button);
    expect(button.getAttribute("aria-label")).not.toBe(initialLabel);
  });
});
