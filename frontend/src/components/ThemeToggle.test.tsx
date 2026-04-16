import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { ThemeProvider } from "./ThemeProvider";
import { ThemeToggle } from "./ThemeToggle";

describe("ThemeToggle", () => {
  it("renders and toggles theme", async () => {
    const user = userEvent.setup();
    render(
      <ThemeProvider>
        <ThemeToggle />
      </ThemeProvider>
    );

    const button = screen.getByRole("button");
    expect(button).toBeDefined();
    
    const initialLabel = button.getAttribute("aria-label");
    await user.click(button);
    expect(button.getAttribute("aria-label")).not.toBe(initialLabel);
  });
});
