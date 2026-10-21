import { expect, test } from "vitest";
import { userEvent } from "vitest/browser";
import { render } from "vitest-browser-react";
import "../styles/tokens.css";
import { Button } from "./Button";
import { Shortcut } from "./Tooltip";

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
