import { expect, test, vi } from "vitest";
import { render } from "vitest-browser-react";
import "../../styles/tokens.css";
import { emulateTouchScreen } from "../../testing";
import { Drop } from "./Drop";

/** The landing as it waits for a file, with nothing kept in this browser. */
function waiting() {
  return render(
    <Drop
      landing={{ kind: "waiting" }}
      kept={null}
      forgetFailed={null}
      onChoose={vi.fn()}
      onReopen={vi.fn()}
      onForget={vi.fn()}
    />,
  );
}

test("under the sheet, the landing promises the file is deleted, and only the top heading's typed word takes a caret that blinks five times", async () => {
  const screen = await waiting();
  await expect
    .element(screen.getByRole("heading", { level: 2, name: "We delete your file an hour after you're done." }))
    .toBeVisible();
  const carets = [...document.querySelectorAll("mark")].map((mark) => getComputedStyle(mark, "::after"));
  expect(carets.map((caret) => caret.animationIterationCount)).toEqual(["5", "1"]);
});

test("the heading asks for a PDF dropped on it", async () => {
  const screen = await waiting();
  await expect.element(screen.getByRole("heading", { level: 1 })).toHaveAccessibleName("Drop a PDF here");
});

test("on a touch screen, where a file is seldom dropped, the heading asks for one opened", async () => {
  await emulateTouchScreen();
  const screen = await waiting();
  await expect.element(screen.getByRole("heading", { level: 1 })).toHaveAccessibleName("Open a PDF here");
});

test("the promise's line links the code this page was built from", async () => {
  const screen = await waiting();
  await expect
    .element(screen.getByRole("link", { name: "read the code that does it" }))
    .toHaveAttribute("href", __SOURCE_CODE__);
  // A commit's tree in the repository, or the repository itself where the build had no commit to name.
  expect(__SOURCE_CODE__).toMatch(/^https:\/\/github\.com\/kevinvle1997\/squid-pdf(\/tree\/\S+)?$/);
});
