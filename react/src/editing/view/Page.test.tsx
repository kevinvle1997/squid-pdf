import { beforeEach, describe, expect, test, vi } from "vitest";
import { userEvent } from "vitest/browser";
import { render } from "vitest-browser-react";
import "../../styles/tokens.css";
import "../../styles/base.css";
import { render as renderOnServer } from "../../api/client";
import type { Render } from "../../api/types";
import { A4, aDoc, aFont, aSpan } from "../../fixtures";
import { createEditor, type Editor } from "../editor";
import { EditorContext } from "./context";
import { Page } from "./Page";

vi.mock(import("../../api/client"), async (original) => ({
  ...(await original()),
  render: vi.fn(),
  stillThere: vi.fn(async () => true),
}));

// The page draws its margin twice each time it's drawn itself, so counting the margin counts the page.
const drawn = vi.hoisted(() => ({ margins: 0 }));
vi.mock(import("./Margin"), async (original) => {
  const { Margin } = await original();
  return {
    Margin: (props: Parameters<typeof Margin>[0]) => {
      drawn.margins++;
      return Margin(props);
    },
  };
});

const WORDS = "was here";
const span = aSpan({ id: "s1", text: WORDS, size: 20, bbox: { x0: 72, y0: 100, x1: 152, y1: 124 }, origin: [72, 120] });
// Every letter half the size wide: the span's eight are 80 pt at 20 pt.
const glyphs = Object.fromEntries([..."abcdefghijklmnopqrstuvwxyz "].map((letter) => [letter, 500]));
const DOC = aDoc({ spans: [span], fonts: [aFont("Times-Roman", { glyphs })] });

let editor: Editor;

beforeEach(() => {
  vi.mocked(renderOnServer)
    .mockReset()
    .mockReturnValue(new Promise<Render>(() => undefined));
  editor = createEditor(new File(["%PDF-"], "contract.pdf"), DOC, 2);
});

async function draw() {
  return render(
    <EditorContext.Provider value={editor}>
      <Page index={0} info={A4} />
    </EditorContext.Provider>,
  );
}

/** Marks come once the page is near the viewport, which its observer says a moment after drawing. */
async function editSpan(screen: Awaited<ReturnType<typeof draw>>) {
  const mark = screen.getByRole("button", { name: WORDS });
  await expect.element(mark).toBeInTheDocument();
  mark.element().focus();
  await userEvent.keyboard("{Enter}");
}

describe("a page", () => {
  test("marks each span as a button named for its words", async () => {
    const screen = await draw();
    await expect.element(screen.getByRole("region", { name: "Page 1" })).toBeVisible();
    await expect.element(screen.getByRole("button", { name: WORDS })).toBeInTheDocument();
  });

  test("Enter on a span opens a field in its place, and too long says by how much, in --warn", async () => {
    const screen = await draw();
    await editSpan(screen);
    const field = screen.getByRole("textbox", { name: `Change “${WORDS}”` });
    await expect.element(field).toHaveFocus();
    await userEvent.keyboard("{End}");
    await userEvent.keyboard("xyz");
    // Three letters of 10 pt past 80 pt: 30 pt too long, well past the tolerance.
    const said = screen.getByText("30.0 pt too long");
    await expect.element(said).toBeVisible();
    const warn = document.body.appendChild(document.createElement("span"));
    warn.style.color = "var(--warn)";
    expect(getComputedStyle(said.element()).color).toBe(getComputedStyle(warn).color);
  });

  test("a kept edit gets a margin note that puts it back, and focus returns to the span", async () => {
    const screen = await draw();
    await editSpan(screen);
    await screen.getByRole("textbox").fill("is here");
    await userEvent.keyboard("{Enter}");
    expect(vi.mocked(renderOnServer)).toHaveBeenCalledTimes(1);

    const note = screen.getByRole("button", { name: `Undo: “is here” goes back to “${WORDS}”` });
    await note.click();
    await expect.element(note).not.toBeInTheDocument();
    expect(editor.store.get().view.spans.size).toBe(0);
    await expect.element(screen.getByRole("button", { name: WORDS })).toHaveFocus();
  });

  test("typing redraws the field alone: the page it's on isn't drawn again", async () => {
    const screen = await draw();
    await editSpan(screen);
    const field = screen.getByRole("textbox", { name: `Change “${WORDS}”` });
    await expect.element(field).toHaveFocus();
    const before = drawn.margins;
    await userEvent.keyboard("{End}abc");
    await expect.element(field).toHaveValue(`${WORDS}abc`);
    expect(drawn.margins).toBe(before);
  });
});
