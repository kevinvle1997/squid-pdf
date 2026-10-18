import { afterEach, expect, test } from "vitest";
import { render } from "vitest-browser-react";
import "../styles/tokens.css";
import { colorOf } from "../testing";
import { Button } from "./Button";
import { Sheet } from "./Sheet";
import { Warn } from "./Warn";

afterEach(() => {
  delete document.documentElement.dataset.theme;
});

test("what's set on a sheet reads as on white, in the dark theme too", async () => {
  document.documentElement.dataset.theme = "dark";
  const screen = await render(
    <Sheet>
      <Warn>no é in this font</Warn>
      <Button>Reload</Button>
    </Sheet>,
  );
  expect(getComputedStyle(screen.getByText("no é in this font").element()).color).toBe(colorOf("--mark-warn"));
  expect(getComputedStyle(screen.getByRole("button").element()).color).toBe(colorOf("--sheet-ink"));
});

test("the sheet's own edge keeps the theme's tokens, so a ring on the ground around it stays visible", async () => {
  document.documentElement.dataset.theme = "dark";
  const screen = await render(<Sheet>Drop a PDF</Sheet>);
  const sheet = screen.container.firstElementChild as HTMLElement;
  sheet.style.outlineColor = "var(--change)";
  expect(getComputedStyle(sheet).outlineColor).toBe(colorOf("--change"));
  expect(getComputedStyle(sheet, "::before").backgroundImage).toContain(colorOf("--ink-faint"));
});
