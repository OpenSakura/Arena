import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { createTestI18n, TestI18nProvider } from "@/i18n/test-utils";

import ErrorBoundary from "./ErrorBoundary";

async function renderErrorBoundary(error: Error, locale: "en" | "zh" = "en") {
  const i18n = await createTestI18n(locale);
  const reset = vi.fn();

  render(
    <TestI18nProvider i18n={i18n}>
      <ErrorBoundary error={error} reset={reset} />
    </TestI18nProvider>,
  );

  return { reset };
}

describe("ErrorBoundary", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("localizes fallback text and retry action", async () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    const { reset } = await renderErrorBoundary(new Error(""), "zh");

    expect(screen.getByRole("heading", { name: "出错了" })).toBeDefined();
    expect(screen.getByText("出现意外错误")).toBeDefined();

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "重试" }));

    expect(reset).toHaveBeenCalledTimes(1);
  });

  it("keeps provided error details literal", async () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);

    await renderErrorBoundary(new Error("Backend detail 42"));

    expect(screen.getByRole("heading", { name: "Something went wrong" })).toBeDefined();
    expect(screen.getByText("Backend detail 42")).toBeDefined();
  });
});
