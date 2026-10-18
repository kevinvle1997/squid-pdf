import { expect, test } from "vitest";
import { render } from "vitest-browser-react";
import "../styles/tokens.css";
import { colorOf } from "../testing";
import { Warn } from "./Warn";

test("a warning is in --warn and bold, so it never reads as decoration", async () => {
  const screen = await render(<Warn>no é in this font</Warn>);
  const words = getComputedStyle(screen.getByText("no é in this font").element());
  expect(words.color).toBe(colorOf("--warn"));
  expect(words.fontWeight).toBe("600");
});

test("the warning mark is for the eye; the words say it to a screen reader", async () => {
  const screen = await render(<Warn mark>4.4 pt too long</Warn>);
  expect(screen.container.querySelector("svg")?.getAttribute("aria-hidden")).toBe("true");
  await expect.element(screen.getByText("4.4 pt too long")).toBeVisible();
});
