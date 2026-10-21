import { afterEach, expect, test } from "vitest";
import { render } from "vitest-browser-react";
import "../styles/tokens.css";
import { ThemeToggle } from "./ThemeToggle";
import { showPickedTheme } from "./theme";

afterEach(() => {
  delete document.documentElement.dataset.theme;
  localStorage.clear();
});

test("pressed, it turns the interface dark and back, and the next visit opens in the theme picked", async () => {
  document.documentElement.dataset.theme = "light";
  const screen = await render(<ThemeToggle />);
  const toggle = screen.getByRole("button", { name: "Dark theme" });
  await expect.element(toggle).toHaveAttribute("aria-pressed", "false");
  await toggle.click();
  await expect.element(toggle).toHaveAttribute("aria-pressed", "true");
  expect(getComputedStyle(document.documentElement).getPropertyValue("--bar").trim()).toBe("#222A26");
  delete document.documentElement.dataset.theme;
  showPickedTheme();
  expect(document.documentElement.dataset.theme).toBe("dark");
  await toggle.click();
  expect(document.documentElement.dataset.theme).toBe("light");
  expect(getComputedStyle(document.documentElement).getPropertyValue("--bar").trim()).toBe("#CBD3CE");
});
