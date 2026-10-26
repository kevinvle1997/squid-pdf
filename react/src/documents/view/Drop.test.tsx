import { expect, test, vi } from "vitest";
import { render } from "vitest-browser-react";
import "../../styles/tokens.css";
import { aDoc } from "../../fixtures";
import { emulateTouchScreen } from "../../testing";
import { Drop } from "./Drop";

/** The landing as it waits for a file, with nothing persisted in this browser. */
function waiting() {
  return render(
    <Drop
      landing={{ kind: "waiting" }}
      persisted={null}
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

test("a document persisted here is offered to open again, with deleting it quiet beside that", async () => {
  const persisted = { file: new File(["%PDF-"], "contract.pdf"), doc: aDoc(), work: null, until: Date.now() + 60_000 };
  const screen = await render(
    <Drop
      landing={{ kind: "waiting" }}
      persisted={persisted}
      forgetFailed={null}
      onChoose={vi.fn()}
      onReopen={vi.fn()}
      onForget={vi.fn()}
    />,
  );
  const look = (name: string) => {
    const style = getComputedStyle(screen.getByRole("button", { name }).element());
    return { border: style.borderColor, fill: style.backgroundColor };
  };
  await expect.element(screen.getByRole("button", { name: "Delete it now" })).toBeVisible();
  // Neither border nor fill: not the twin of Open's bordered button, nor a second primary.
  expect(look("Delete it now")).toEqual({ border: "rgba(0, 0, 0, 0)", fill: "rgba(0, 0, 0, 0)" });
  expect(look("Open it again").border).not.toBe("rgba(0, 0, 0, 0)");
});

test("the promise's line links the code this page was built from", async () => {
  const screen = await waiting();
  await expect
    .element(screen.getByRole("link", { name: "read the code that does it" }))
    .toHaveAttribute("href", __SOURCE_CODE__);
  // A commit's tree in the repository, or the repository itself where the build had no commit to name.
  expect(__SOURCE_CODE__).toMatch(/^https:\/\/github\.com\/kevinvle1997\/squid-pdf(\/tree\/\S+)?$/);
});
