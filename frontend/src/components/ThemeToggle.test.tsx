import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

import { ThemeToggle } from "./ThemeToggle";
import * as ThemeProviderModule from "@/components/ThemeProvider";

vi.mock("@/components/ThemeProvider", () => ({
  useTheme: vi.fn(),
}));

describe("ThemeToggle", () => {
  const toggleThemeMock = vi.fn();

  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("renders correctly before hydration (mounted: false)", () => {
    vi.spyOn(ThemeProviderModule, "useTheme").mockReturnValue({
      theme: "light",
      mounted: false,
      setTheme: vi.fn(),
      toggleTheme: toggleThemeMock,
    });

    render(<ThemeToggle />);

    const button = screen.getByRole("button", { name: "Toggle theme" });
    expect(button).toBeDefined();

    // Verify icons have the anti-hydration-flash Tailwind classes
    const icons = button.querySelectorAll("svg");
    expect(icons.length).toBe(2);

    const sunIcon = icons[0];
    const moonIcon = icons[1];

    expect(sunIcon.className.baseVal).toContain("opacity-0");
    expect(sunIcon.className.baseVal).toContain("dark:opacity-100");

    expect(moonIcon.className.baseVal).toContain("opacity-100");
    expect(moonIcon.className.baseVal).toContain("dark:opacity-0");
  });

  it("renders light mode correctly after hydration (mounted: true)", () => {
    vi.spyOn(ThemeProviderModule, "useTheme").mockReturnValue({
      theme: "light",
      mounted: true,
      setTheme: vi.fn(),
      toggleTheme: toggleThemeMock,
    });

    render(<ThemeToggle />);

    const button = screen.getByRole("button", { name: "Switch to dark theme" });
    expect(button).toBeDefined();

    const icons = button.querySelectorAll("svg");
    expect(icons[0].className.baseVal).toContain("opacity-0");
    expect(icons[1].className.baseVal).toContain("opacity-100");
  });

  it("renders dark mode correctly after hydration (mounted: true)", () => {
    vi.spyOn(ThemeProviderModule, "useTheme").mockReturnValue({
      theme: "dark",
      mounted: true,
      setTheme: vi.fn(),
      toggleTheme: toggleThemeMock,
    });

    render(<ThemeToggle />);

    const button = screen.getByRole("button", { name: "Switch to light theme" });
    expect(button).toBeDefined();

    const icons = button.querySelectorAll("svg");
    expect(icons[0].className.baseVal).toContain("opacity-100");
    expect(icons[1].className.baseVal).toContain("opacity-0");
  });

  it("calls toggleTheme on click", () => {
    vi.spyOn(ThemeProviderModule, "useTheme").mockReturnValue({
      theme: "dark",
      mounted: true,
      setTheme: vi.fn(),
      toggleTheme: toggleThemeMock,
    });

    render(<ThemeToggle />);

    const button = screen.getByRole("button");
    fireEvent.click(button);

    expect(toggleThemeMock).toHaveBeenCalledTimes(1);
  });
});
