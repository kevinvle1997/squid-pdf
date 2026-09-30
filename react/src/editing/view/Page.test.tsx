import { beforeEach, describe, expect, test, vi } from "vitest";
import { userEvent } from "vitest/browser";
import { render } from "vitest-browser-react";
import "../../styles/tokens.css";
import "../../styles/base.css";
import { render as renderOnServer } from "../../api/client";
import type { Document, FontInfo, Render, SpanInfo } from "../../api/types";
import { type Editor, createEditor } from "../editor";
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
const span: SpanInfo = {
  id: "s1",
  page: 0,
  text: WORDS,
  font: "Times-Roman",
  size: 20,
  color: [0, 0, 0],
  bbox: { x0: 72, y0: 100, x1: 152, y1: 124 },
  origin: [72, 120],
  fidelity: "exact",
};
// Every letter half the size wide: the span's eight are 80 pt at 20 pt.
const font = {
  name: "Times-Roman",
  substitute: null,
  why: null,
  same_widths: true,
  glyphs: Object.fromEntries([..."abcdefghijklmnopqrstuvwxyz "].map((letter) => [letter, 500])),
} as unknown as FontInfo;
const DOC = {
  id: "doc",
  build: "b",
  pages: [{ width: 595, height: 842, rotation: 0 }],
  spans: [span],
  fonts: [font],
  fit: { tolerance_pt: 4, condense_limit: 0.05, shrink_floor: 0.9 },
  copy: { missing: "no {chars} in this font", too_long: "{delta_pt} pt too long", reopened: "", export_left_out: "" },
  notices: [],
} as unknown as Document;

let editor: Editor;

beforeEach(() => {
  vi.mocked(renderOnServer).mockReset().mockReturnValue(new Promise<Render>(() => undefined));
  editor = createEditor(new File(["%PDF-"], "contract.pdf"), DOC, 2);
});

async function draw() {
  return render(
    <EditorContext.Provider value={editor}>
      <Page index={0} info={DOC.pages[0]!} />
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
