import { expect, test } from "vitest";
import { cdp, userEvent } from "vitest/browser";
import { render } from "vitest-browser-react";
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
  const at = { x: box.x + box.width / 2, y: box.y + box.height / 2 };
  await cdp().send("Input.dispatchMouseEvent", { type: "mouseMoved", ...at });
  // Past the delay a note closes after: still there, under the pointer.
  await new Promise((closed) => setTimeout(closed, 1000));
  await expect.element(note).toBeVisible();
});
