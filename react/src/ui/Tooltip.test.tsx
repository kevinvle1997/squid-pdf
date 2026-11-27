import { expect, test } from "vitest";
import { cdp, userEvent } from "vitest/browser";
import { render } from "vitest-browser-react";
import { middleOf } from "../testing";
import "../styles/tokens.css";
import { Button } from "./Button";
import { Shortcut, Tip } from "./Tooltip";

test("a button's shortcut shows under it as keyboard focus reaches it, and its name stays the button's own", async () => {
  const screen = await render(
    <Shortcut keys="Esc">
      <Button variant="quiet">Close</Button>
    </Shortcut>,
  );
  const button = screen.getByRole("button", { name: "Close" });
  await userEvent.tab();
  await expect.element(button).toHaveFocus();
  await expect.element(screen.getByRole("tooltip")).toHaveTextContent("Esc");
});

test("a note shown on hover stays as the pointer moves onto it, to be read (WCAG 1.4.13, Hoverable)", async () => {
  const screen = await render(
    <div style={{ padding: "120px" }}>
      <Tip heading="Was “14 March”" detail="Now “17 March”">
        <Button>17 March</Button>
      </Tip>
    </div>,
  );
  // A press away from it first: after a key, React Aria opens no note on hover.
  await cdp().send("Input.dispatchMouseEvent", { type: "mousePressed", x: 4, y: 4, button: "left", clickCount: 1 });
  await cdp().send("Input.dispatchMouseEvent", { type: "mouseReleased", x: 4, y: 4, button: "left", clickCount: 1 });
  await userEvent.hover(screen.getByRole("button", { name: "17 March" }));
  const note = screen.getByRole("tooltip");
  await expect.element(note).toBeVisible();
  const box = note.element().getBoundingClientRect();
  const at = middleOf(box);
  await cdp().send("Input.dispatchMouseEvent", { type: "mouseMoved", ...at });
  await new Promise((closed) => setTimeout(closed, 1000));
  await expect.element(note).toBeVisible();
});

test("a note of a change draws an arrow down from what was to what is, which a screen reader doesn't hear", async () => {
  const screen = await render(
    <Tip heading="Was “14 March”" detail="Now “17 March”" changed>
      <Button>17 March</Button>
    </Tip>,
  );
  await userEvent.tab();
  const note = screen.getByRole("tooltip");
  await expect.element(note).toHaveTextContent("Was “14 March”Now “17 March”");
  const arrow = note.element().querySelector("svg");
  expect(arrow?.getAttribute("aria-hidden")).toBe("true");
  expect(arrow?.previousElementSibling?.textContent).toBe("Was “14 March”");
  expect(arrow?.nextSibling?.textContent).toBe("Now “17 March”");
});
