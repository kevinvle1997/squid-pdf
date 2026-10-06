import { beforeEach, describe, expect, test, vi } from "vitest";
import { page, userEvent } from "vitest/browser";
import { render } from "vitest-browser-react";
import "../../styles/tokens.css";
import "../../styles/base.css";
import { render as renderOnServer } from "../../api/client";
import type { PageInfo, Render } from "../../api/types";
import { A4, aDoc, aFit, aFont, aReply, aSpan, COPY } from "../../fixtures";
import { createEditor, type Editor, undo } from "../editor";
import { edit, finish, typeInto } from "../typing";
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
// A line on the next page, to show what a change to the first leaves alone.
const elsewhere = aSpan({ id: "s2", page: 1, text: "was there" });
const DOC = aDoc({ spans: [span, elsewhere], fonts: [aFont("Times-Roman", { glyphs })] });

let editor: Editor;

beforeEach(() => {
  vi.mocked(renderOnServer)
    .mockReset()
    .mockReturnValue(new Promise<Render>(() => undefined));
  editor = createEditor(new File(["%PDF-"], "contract.pdf"), DOC, 2);
});

async function draw(index = 0, info = A4) {
  return render(
    <EditorContext.Provider value={editor}>
      <Page index={index} info={info} />
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

  test("Enter keeps the edit and puts focus back on the span; its margin note puts it back, and focus with it", async () => {
    const screen = await draw();
    await editSpan(screen);
    await screen.getByRole("textbox").fill("is here");
    await userEvent.keyboard("{Enter}");
    expect(vi.mocked(renderOnServer)).toHaveBeenCalledTimes(1);
    await expect.element(screen.getByRole("button", { name: "is here" })).toHaveFocus();

    const note = screen.getByRole("button", { name: `Undo: “is here” goes back to “${WORDS}”` });
    await note.click();
    await expect.element(note).not.toBeInTheDocument();
    expect(editor.store.get().reading.spans.size).toBe(0);
    await expect.element(screen.getByRole("button", { name: WORDS })).toHaveFocus();
  });

  test("undo brings its span into view once: scrolled away after, the page doesn't pull it back", async () => {
    const screen = await draw();
    await editSpan(screen);
    await screen.getByRole("textbox").fill("is here");
    await userEvent.keyboard("{Enter}");
    const below = document.createElement("div");
    below.style.height = "20000px";
    document.body.append(below);
    undo(editor);
    await vi.waitFor(() => expect(window.scrollY).toBeLessThan(1000));
    window.scrollTo(0, 15000);
    // The page draws again: its image goes as it leaves, and the server's verdict lands.
    const sheet = screen.getByRole("region", { name: "Page 1" }).element();
    await vi.waitFor(() => expect(sheet.querySelector("img")).toBeNull());
    editor.store.set({ drawn: { ...editor.store.get().drawn, fits: new Map([[0, { s1: aFit({}) }]]) } });
    await new Promise((resolve) => setTimeout(resolve, 300));
    expect(window.scrollY).toBe(15000);
    below.remove();
  });

  test("the field stays while the page it's on is scrolled far away", async () => {
    const screen = await draw();
    await editSpan(screen);
    const field = screen.getByRole("textbox");
    await expect.element(field).toBeInTheDocument();
    // Far enough below that the page is past the margin pages are drawn within.
    const below = document.createElement("div");
    below.style.height = "20000px";
    document.body.append(below);
    window.scrollTo(0, 15000);
    // The image goes as the page leaves; the field the user typed in doesn't.
    const sheet = screen.getByRole("region", { name: "Page 1" }).element();
    await vi.waitFor(() => expect(sheet.querySelector("img")).toBeNull());
    await expect.element(field).toBeInTheDocument();
    below.remove();
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

  test("the server's render of one page doesn't draw another again", async () => {
    let land: (reply: Render) => void = () => undefined;
    vi.mocked(renderOnServer).mockReturnValue(new Promise<Render>((resolve) => (land = resolve)));
    const screen = await draw(1);
    await expect.element(screen.getByRole("button", { name: "was there" })).toBeInTheDocument();
    edit(editor, span.id, null);
    typeInto(editor, "is here");
    finish(editor, true);
    const before = drawn.margins;
    land(aReply({ fits: { [span.id]: aFit() } }));
    await expect.poll(() => editor.store.get().drawn.from.has(0)).toBe(true);
    expect(drawn.margins).toBe(before);
  });

  test("a page wider than A4 shows at its printed size too", async () => {
    await page.viewport(1400, 900);
    const letter: PageInfo = { width: 612, height: 792, turn_cw: 0 };
    const screen = await draw(0, letter);
    const sheet = screen.getByRole("region", { name: "Page 1" }).element().children[1];
    // 612 pt at 96 dpi is 816 px.
    expect(sheet?.getBoundingClientRect().width).toBeCloseTo(816, 0);
  });

  test("a span in a similar font shares the page's note: keyboard focus shows it, Escape closes it, Enter still edits", async () => {
    const substituted = aSpan({
      id: "s3",
      text: "in Arial",
      font: "Arial",
      bbox: { x0: 72, y0: 300, x1: 152, y1: 324 },
    });
    const substitutedFont = aFont("Arial", {
      substitute: "Liberation Sans Regular",
      same_widths: false,
      why: "Not embedded.",
    });
    editor = createEditor(
      new File(["%PDF-"], "contract.pdf"),
      aDoc({ spans: [substituted], fonts: [substitutedFont] }),
      2,
    );
    const screen = await draw();
    const mark = screen.getByRole("button", { name: "in Arial" });
    await expect.element(mark).toBeInTheDocument();
    // Focus from the keyboard: a key pressed first, as Tab would be.
    await userEvent.keyboard("{Shift}");
    mark.element().focus();
    const note = screen.getByText("Edits here use Liberation Sans Regular", { exact: false });
    await expect.element(note).toBeVisible();
    await expect.element(mark).toHaveAccessibleDescription(/Edits here use Liberation Sans Regular/);

    await userEvent.keyboard("{Escape}");
    await expect.element(note).not.toBeInTheDocument();
    await userEvent.keyboard("{Enter}");
    await expect.element(screen.getByRole("textbox", { name: "Change “in Arial”" })).toHaveFocus();
  });

  test("text a form field draws has the dashed warning line, and its note says an edit is left out", async () => {
    const field = aSpan({ id: "s3", text: "SSN 078", form_field: true, bbox: { x0: 72, y0: 300, x1: 152, y1: 324 } });
    editor = createEditor(
      new File(["%PDF-"], "contract.pdf"),
      aDoc({ spans: [field], fonts: [aFont("Times-Roman")] }),
      2,
    );
    const screen = await draw();
    const mark = screen.getByRole("button", { name: "SSN 078" });
    await expect.element(mark).toBeInTheDocument();
    const line = getComputedStyle(mark.element(), "::after");
    expect(line.backgroundImage).toContain("repeating-linear-gradient");
    await mark.hover();
    await expect.element(screen.getByText(COPY.form_field_not_edited)).toBeVisible();
  });

  test("text an edit won't match has the dashed warning line, and its note says why, warned", async () => {
    const turned = aSpan({
      id: "s4",
      text: "Turned",
      fidelity: "approximate",
      why: { code: "turned_text", params: {} },
      bbox: { x0: 72, y0: 300, x1: 152, y1: 324 },
    });
    editor = createEditor(
      new File(["%PDF-"], "contract.pdf"),
      aDoc({ spans: [turned], fonts: [aFont("Times-Roman")] }),
      2,
    );
    const screen = await draw();
    const mark = screen.getByRole("button", { name: "Turned" });
    await expect.element(mark).toBeInTheDocument();
    const line = getComputedStyle(mark.element(), "::after");
    expect(line.backgroundImage).toContain("repeating-linear-gradient");
    await mark.hover();
    const note = screen.getByText(COPY.approximate.turned_text ?? "");
    await expect.element(note).toBeVisible();
    const warn = document.body.appendChild(document.createElement("span"));
    warn.style.color = "var(--warn)";
    expect(getComputedStyle(note.element()).color).toBe(getComputedStyle(warn).color);
    warn.remove();
  });

  test("a click shows a span's note; a click's focus alone doesn't", async () => {
    const substituted = aSpan({
      id: "s3",
      text: "in Arial",
      font: "Arial",
      bbox: { x0: 72, y0: 300, x1: 152, y1: 324 },
    });
    const substitutedFont = aFont("Arial", { substitute: "Liberation Sans Regular" });
    editor = createEditor(
      new File(["%PDF-"], "contract.pdf"),
      aDoc({ spans: [substituted], fonts: [substitutedFont] }),
      2,
    );
    const screen = await draw();
    const mark = screen.getByRole("button", { name: "in Arial" });
    await expect.element(mark).toBeInTheDocument();
    const note = screen.getByText("Edits here use Liberation Sans Regular", { exact: false });
    // A pointer was used last: focus that comes without a press is a click's, not the keyboard's.
    await userEvent.click(screen.getByRole("region", { name: "Page 1" }), { position: { x: 1, y: 1 } });
    mark.element().focus();
    await new Promise((resolve) => setTimeout(resolve, 50));
    await expect.element(note).not.toBeInTheDocument();
    await mark.click();
    await expect.element(note).toBeVisible();
  });
});
