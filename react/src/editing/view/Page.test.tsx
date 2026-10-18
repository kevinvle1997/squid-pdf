import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { cdp, page, userEvent } from "vitest/browser";
import { render } from "vitest-browser-react";
import "@fontsource/atkinson-hyperlegible-next/600.css";
import "../../styles/tokens.css";
import "../../styles/base.css";
import { ProblemError, pageImage, renderStrips } from "../../api/client";
import type { Document, ImageInfo, PageInfo, Render } from "../../api/types";
import { A4, aDoc, aFit, aFont, aProblem, aRedact, aReply, aSpan, COPY, halfWideGlyphs } from "../../fixtures";
import { colorOf } from "../../testing";
import { PX_PER_PT } from "../constants";
import { createEditor, type Editor, undo } from "../editor";
import { openFind, typeQuery } from "../find";
import { edit, finish, stepWayOut, typeInto } from "../typing";
import { EditorContext } from "./context";
import { Page } from "./Page";

// What's kept in this browser is the App's to test, in the browser's real storage.
vi.mock(import("../../documents/kept"), async (original) => ({
  ...(await original()),
  keepFile: vi.fn(async () => undefined),
  keepState: vi.fn(async () => undefined),
}));

vi.mock(import("../../api/client"), async (original) => ({
  ...(await original()),
  renderStrips: vi.fn(),
  stillThere: vi.fn(async () => true),
  pageImage: vi.fn(async () => undefined),
}));

// The page draws its margin each time it's drawn itself, so counting the margin counts the page.
const drawn = vi.hoisted(() => ({ margins: 0 }));
vi.mock(import("./Margin"), async (original) => {
  const module = await original();
  return {
    ...module,
    Margin: (props: Parameters<typeof module.Margin>[0]) => {
      drawn.margins++;
      return module.Margin(props);
    },
  };
});

const WORDS = "was here";
const span = aSpan({ id: "s1", text: WORDS, size: 20, bbox: { x0: 72, y0: 100, x1: 152, y1: 124 }, origin: [72, 120] });
// Every letter half the size wide: the span's eight are 80 pt at 20 pt.
const glyphs = halfWideGlyphs("abcdefghijklmnopqrstuvwxyz ");
// A line on the next page, to show what a change to the first leaves alone.
const elsewhere = aSpan({ id: "s2", page: 1, text: "was there" });
const DOC = aDoc({ spans: [span, elsewhere], fonts: [aFont("Times-Roman", { glyphs })] });

const FILE = new File(["%PDF-"], "contract.pdf");
// The viewport the file starts in, put back after each test so none runs at the size another set.
const VIEWPORT = { width: window.innerWidth, height: window.innerHeight };

let editor: Editor;
// What `farAway` adds below the page, taken out after the test even when it fails.
let spacer: HTMLElement | null = null;

/** Open `doc` in the editor the page draws from, in place of DOC. */
function openDocument(doc: Document) {
  editor = createEditor(FILE, doc, 2);
}

beforeEach(() => {
  vi.mocked(renderStrips)
    .mockReset()
    .mockReturnValue(new Promise<Render>(() => undefined));
  openDocument(DOC);
});

afterEach(async () => {
  spacer?.remove();
  spacer = null;
  await page.viewport(VIEWPORT.width, VIEWPORT.height);
});

async function draw(index = 0, info = A4) {
  return render(
    <EditorContext.Provider value={editor}>
      <Page index={index} info={info} />
    </EditorContext.Provider>,
  );
}

/**
 * Wait until the page has stopped drawing. Its observers report a frame or more after layout, later
 * on a busy machine, and the page draws again for each: a count taken before then counts them too.
 */
async function settled() {
  let count: number;
  do {
    count = drawn.margins;
    await new Promise(requestAnimationFrame);
    await new Promise(requestAnimationFrame);
  } while (drawn.margins !== count);
}

/** WCAG contrast against the white page of a line drawn in its gradient's colour at its opacity. */
function contrastOnWhite(line: CSSStyleDeclaration): number {
  const [red = 0, green = 0, blue = 0] = (/rgb\((\d+), (\d+), (\d+)\)/.exec(line.backgroundImage) ?? [])
    .slice(1)
    .map(Number);
  const opacity = Number(line.opacity);
  const linear = (channel: number) => {
    const shown = (channel * opacity + 255 * (1 - opacity)) / 255;
    return shown <= 0.04045 ? shown / 12.92 : ((shown + 0.055) / 1.055) ** 2.4;
  };
  const luminance = 0.2126 * linear(red) + 0.7152 * linear(green) + 0.0722 * linear(blue);
  return 1.05 / (luminance + 0.05);
}

/** Marks come once the page is near the viewport, which its observer says a moment after drawing. */
async function editSpan(screen: Awaited<ReturnType<typeof draw>>, words = WORDS) {
  const mark = screen.getByRole("button", { name: words });
  await expect.element(mark).toBeInTheDocument();
  mark.element().focus();
  await userEvent.keyboard("{Enter}");
}

/** Scroll the page far enough away that it's past the margin pages are drawn within. */
function farAway() {
  spacer = document.createElement("div");
  spacer.style.height = "20000px";
  document.body.append(spacer);
  window.scrollTo(0, 15000);
}

/** The sheet of the page `element` is on: the box after its margin that the page is drawn in. */
function sheetOf(element: Element): Element {
  const sheet = element.closest("section")?.children[1];
  if (sheet === undefined) throw new Error("no sheet");
  return sheet;
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
    expect(getComputedStyle(said.element()).color).toBe(colorOf("--warn"));
  });

  test("a line a little past its room offers the ways out; one pressed is drawn in the field at once, focus staying", async () => {
    // Twenty letters at 5 pt each is 100 pt; one more runs 5 pt past, where every way out is offered.
    const letters = "abcdefghijklmnopqrst";
    const long = aSpan({ id: "long", text: letters, bbox: { x0: 72, y0: 100, x1: 172, y1: 112 }, origin: [72, 110] });
    openDocument(aDoc({ spans: [long], fonts: [aFont("Times-Roman", { glyphs })] }));
    const screen = await draw();
    await editSpan(screen, letters);
    const field = screen.getByRole("textbox");
    await userEvent.keyboard("{End}u");

    const ways = screen.getByRole("radiogroup", { name: "How it fits" });
    await expect.element(ways).toBeVisible();
    await expect.element(ways.getByRole("radio", { name: "Leave it long" })).toBeChecked();
    await expect.element(screen.getByText("5.0 pt too long")).toBeVisible();
    const fontSize = () => parseFloat(getComputedStyle(field.element()).fontSize);
    const asTyped = fontSize();

    await ways.getByRole("radio", { name: "Make it slightly smaller" }).click();
    await expect.element(field).toHaveFocus();
    expect(fontSize()).toBeCloseTo((asTyped * 100) / 105, 1);
    await expect.element(screen.getByText("5.0 pt too long")).not.toBeInTheDocument();

    // The field's own keys choose too: the next is tightening the letters, at the size typed.
    await userEvent.keyboard("{ArrowDown}");
    await expect.element(ways.getByRole("radio", { name: "Tighten the letters" })).toBeChecked();
    expect(fontSize()).toBeCloseTo(asTyped, 1);
    expect(getComputedStyle(field.element()).transform).not.toBe("none");
    await expect.element(field).toHaveFocus();
  });

  test("an edit drawn smaller previews smaller, before its render lands", async () => {
    const letters = "abcdefghijklmnopqrst";
    const long = aSpan({ id: "long", text: letters, bbox: { x0: 72, y0: 100, x1: 172, y1: 112 }, origin: [72, 110] });
    openDocument(aDoc({ spans: [long], fonts: [aFont("Times-Roman", { glyphs })] }));
    const screen = await draw();
    await expect.element(screen.getByRole("button", { name: letters })).toBeInTheDocument();
    edit(editor, long.id, null);
    typeInto(editor, `${letters}u`);
    await expect.element(screen.getByRole("textbox")).toBeInTheDocument();
    const asTyped = parseFloat(getComputedStyle(screen.getByRole("textbox").element()).fontSize);
    stepWayOut(editor, 1);
    finish(editor, true);

    const preview = () => document.querySelector<HTMLElement>("[data-preview]");
    await expect.poll(() => preview()?.textContent).toBe(`${letters}u`);
    expect(parseFloat(getComputedStyle(preview() ?? document.body).fontSize)).toBeCloseTo((asTyped * 100) / 105, 1);
  });

  test("a number in the chip's sentence keeps its dot tight, as running text does", async () => {
    const screen = await draw();
    await editSpan(screen);
    await userEvent.keyboard("{End}xyz");
    const said = screen.getByText("30.0 pt too long");
    await expect.element(said).toBeVisible();
    await document.fonts.ready;
    const words = said.element().lastChild;
    if (!(words instanceof Text)) throw new Error("no sentence in the chip");
    const widthOf = (at: number) => {
      const letter = document.createRange();
      letter.setStart(words, at);
      letter.setEnd(words, at + 1);
      return letter.getBoundingClientRect().width;
    };
    // The interface face's own dot is half a digit wide; tabular figures make it a whole one.
    const at = words.data.indexOf(".");
    expect(widthOf(at)).toBeLessThan(widthOf(at - 1) * 0.75);
  });

  test("the field names the font that draws it, and a missing letter the face the server draws it in", async () => {
    // Kept in the file, trimmed to the letters it draws: the server falls back to the Lato we ship.
    const lato = aFont("ABCDEE+Lato-Bold", { look_alike: "Lato Bold", glyphs });
    const bold = aSpan({ ...span, font: lato.name });
    openDocument(aDoc({ spans: [bold], fonts: [lato] }));
    const screen = await draw();
    await editSpan(screen);
    await expect.element(screen.getByText("Lato-Bold", { exact: true })).toBeVisible();
    await userEvent.keyboard("{End}Q");
    await expect.element(screen.getByText("no Q in this font, so the line is drawn in our Lato Bold")).toBeVisible();
  });

  test("a span in a right-hand column has its chip under it, ending where it ends, not squeezed against the page's edge", async () => {
    await page.viewport(1400, 900);
    const amount = aSpan({ ...span, text: "was here", bbox: { x0: 480, y0: 100, x1: 560, y1: 124 } });
    openDocument(aDoc({ spans: [amount], fonts: DOC.fonts }));
    const screen = await draw();
    await editSpan(screen);
    await userEvent.keyboard("{End}Q");
    const redact = screen.getByRole("button", { name: "Redact" });
    await expect.element(redact).toBeVisible();
    const button = redact.element().getBoundingClientRect();
    const chip = redact.element().closest("div")?.getBoundingClientRect();
    const field = screen.getByRole("textbox").element().getBoundingClientRect();
    if (chip === undefined) throw new Error("no chip");
    // Its sentence on one line and its offer and Redact on the next, not wrapped into a column at the page's edge.
    expect(chip.height).toBeLessThan(button.height * 3);
    // Ending where the span does: its box starts where the field does, and the page shows at its printed size.
    expect(chip.right).toBeLessThanOrEqual(field.left + (560 - 480) * PX_PER_PT + 1);
  });

  test("the chip says the verdict on what's typed: it looks identical, or a similar font draws it, warned", async () => {
    const screen = await draw();
    await editSpan(screen);
    await expect.element(screen.getByText("looks identical")).toBeVisible();
    await userEvent.keyboard("{Escape}");

    const arial = aSpan({
      ...span,
      id: "s3",
      text: "in arial",
      font: "Arial",
      fidelity: "substitute",
      substitute: "Liberation Sans Regular",
    });
    openDocument(aDoc({ spans: [arial], fonts: [aFont("Arial", { substitute: "Liberation Sans Regular", glyphs })] }));
    screen.rerender(
      <EditorContext.Provider value={editor}>
        <Page index={0} info={A4} />
      </EditorContext.Provider>,
    );
    await editSpan(screen, "in arial");
    const similar = screen.getByText("a similar font", { exact: true });
    await expect.element(similar).toBeVisible();
    expect(getComputedStyle(similar.element()).color).toBe(colorOf("--warn"));
  });

  test("a line typed past its span keeps its start in view, letters the file's font hasn't got included", async () => {
    const screen = await draw();
    await editSpan(screen);
    await userEvent.keyboard("{End}");
    // Capitals: the span's font has only lower case, so its widths count none of them.
    await userEvent.keyboard(" AND A GOOD DEAL MORE");
    const field = screen.getByRole("textbox").element() as HTMLInputElement;
    await expect.element(screen.getByRole("textbox")).toHaveValue(`${WORDS} AND A GOOD DEAL MORE`);
    // A pixel spare: the field's width in points rounds to whole pixels either way.
    expect(field.scrollWidth).toBeLessThanOrEqual(field.clientWidth + 1);
    expect(field.scrollLeft).toBe(0);
  });

  test("a line typed past the page's edge leaves the page where it is: the sheet never scrolls to the caret", async () => {
    const screen = await draw();
    await editSpan(screen);
    await userEvent.keyboard("{End}");
    // Far past the right edge of the page, as a long replacement runs.
    await userEvent.keyboard(" and a good deal more besides, running on past the edge of the page");
    expect(sheetOf(screen.getByRole("textbox").element()).scrollLeft).toBe(0);
  });

  test("a face that finishes loading after the field opens sizes the field again", async () => {
    const measure = vi.spyOn(CanvasRenderingContext2D.prototype, "measureText");
    measure.mockReturnValue({ width: 0 } as TextMetrics);
    try {
      const screen = await draw();
      await editSpan(screen);
      await userEvent.keyboard("{End} AND MORE");
      const field = screen.getByRole("textbox").element();
      const before = field.clientWidth;
      // As wide as the face that loaded draws it: the font's own widths count none of these capitals.
      measure.mockReturnValue({ width: 400 } as TextMetrics);
      document.fonts.dispatchEvent(new Event("loadingdone"));
      await vi.waitFor(() => expect(field.clientWidth).toBeGreaterThan(before));
    } finally {
      measure.mockRestore();
    }
  });

  test("Enter keeps the edit and puts focus back on the span; its margin note puts it back, and focus with it", async () => {
    const screen = await draw();
    await editSpan(screen);
    await screen.getByRole("textbox").fill("is here");
    await userEvent.keyboard("{Enter}");
    expect(vi.mocked(renderStrips)).toHaveBeenCalledTimes(1);
    await expect.element(screen.getByRole("button", { name: "is here" })).toHaveFocus();

    const note = screen.getByRole("button", { name: `Undo: “is here” goes back to “${WORDS}”` });
    await note.click();
    await expect.element(note).not.toBeInTheDocument();
    expect(editor.store.get().reading.spans.size).toBe(0);
    await expect.element(screen.getByRole("button", { name: WORDS })).toHaveFocus();
  });

  test("Redact in the field's chip takes the whole span out, typed words and all, and its margin note puts it back", async () => {
    await page.viewport(1400, 900);
    const screen = await draw();
    await editSpan(screen);
    await userEvent.keyboard("{End} now");
    await screen.getByRole("button", { name: "Redact" }).click();
    await expect.element(screen.getByRole("textbox")).not.toBeInTheDocument();
    expect(editor.store.get().reading.edits).toEqual([aRedact(span.id)]);

    const note = screen.getByRole("button", { name: `Undo: “${WORDS}” is no longer redacted` });
    await expect.element(note).toBeVisible();
    expect(note.element().textContent).toBe(WORDS);
    await note.click();
    await expect.element(note).not.toBeInTheDocument();
    expect(editor.store.get().reading.edits).toEqual([]);
  });

  test("undo brings its span into view once: scrolled away after, the page doesn't pull it back", async () => {
    const screen = await draw();
    await editSpan(screen);
    await screen.getByRole("textbox").fill("is here");
    await userEvent.keyboard("{Enter}");
    undo(editor);
    await vi.waitFor(() => expect(window.scrollY).toBeLessThan(1000));
    farAway();
    // The page draws again: its image goes as it leaves, and the server's verdict lands.
    const sheet = screen.getByRole("region", { name: "Page 1" }).element();
    await vi.waitFor(() => expect(sheet.querySelector("img")).toBeNull());
    editor.store.set({ drawn: { ...editor.store.get().drawn, fits: new Map([[0, { s1: aFit({}) }]]) } });
    await settled();
    expect(window.scrollY).toBe(15000);
  });

  test("the field stays while the page it's on is scrolled far away", async () => {
    const screen = await draw();
    await editSpan(screen);
    const field = screen.getByRole("textbox");
    await expect.element(field).toBeInTheDocument();
    farAway();
    // The image goes as the page leaves; the field the user typed in doesn't.
    const sheet = screen.getByRole("region", { name: "Page 1" }).element();
    await vi.waitFor(() => expect(sheet.querySelector("img")).toBeNull());
    await expect.element(field).toBeInTheDocument();
  });

  test("a page near the screen is one the render asks for first; scrolled far away, it isn't", async () => {
    await draw(1);
    await vi.waitFor(() => expect(editor.near).toEqual(new Set([1])));
    farAway();
    await vi.waitFor(() => expect(editor.near).toEqual(new Set()));
  });

  test("a span holding focus keeps it while its page is scrolled far away, so focus never falls to the page", async () => {
    const screen = await draw();
    const mark = screen.getByRole("button", { name: WORDS });
    await expect.element(mark).toBeInTheDocument();
    mark.element().focus();
    farAway();
    const sheet = screen.getByRole("region", { name: "Page 1" }).element();
    await vi.waitFor(() => expect(sheet.querySelector("img")).toBeNull());
    await expect.element(mark).toHaveFocus();
  });

  test("on a touch screen the field is set at 16 px, so iOS doesn't zoom, and drawn at the span's size", async () => {
    await cdp().send("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 1 });
    try {
      expect(matchMedia("(pointer: coarse)").matches).toBe(true);
      const screen = await draw();
      await editSpan(screen);
      const field = screen.getByRole("textbox").element();
      expect(getComputedStyle(field).fontSize).toBe("16px");
      // The span's box, as wide as the page is shown: what the field must look like on it.
      const layer = field.closest("section")?.querySelector("img")?.getBoundingClientRect();
      if (layer === undefined) throw new Error("no page image");
      const spanPx = (pt: number) => (pt / A4.width) * layer.width;
      const drawn = field.getBoundingClientRect();
      expect(drawn.height).toBeCloseTo(spanPx(span.bbox.y1 - span.bbox.y0), 0);
      expect(drawn.left).toBeCloseTo(layer.left + spanPx(span.bbox.x0), 0);
      // Its letters drawn at the span's size: 16 px, scaled down.
      const scale = new DOMMatrix(getComputedStyle(field).transform).a;
      expect(16 * scale).toBeCloseTo(spanPx(span.size), 1);
    } finally {
      await cdp().send("Emulation.setTouchEmulationEnabled", { enabled: false });
    }
  });

  test("typing redraws the field alone: the page it's on isn't drawn again", async () => {
    const screen = await draw();
    await editSpan(screen);
    const field = screen.getByRole("textbox", { name: `Change “${WORDS}”` });
    await expect.element(field).toHaveFocus();
    await settled();
    const before = drawn.margins;
    await userEvent.keyboard("{End}abc");
    await expect.element(field).toHaveValue(`${WORDS}abc`);
    expect(drawn.margins).toBe(before);
  });

  test("the server's render of one page doesn't draw another again", async () => {
    let land: (reply: Render) => void = () => undefined;
    vi.mocked(renderStrips).mockReturnValue(new Promise<Render>((resolve) => (land = resolve)));
    const screen = await draw(1);
    await expect.element(screen.getByRole("button", { name: "was there" })).toBeInTheDocument();
    edit(editor, span.id, null);
    typeInto(editor, "is here");
    finish(editor, true);
    await settled();
    const before = drawn.margins;
    land(aReply({ fits: { [span.id]: aFit() } }));
    await expect.poll(() => editor.store.get().drawn.from.has(0)).toBe(true);
    expect(drawn.margins).toBe(before);
  });

  test("a render that says other than its preview settles over its span; one that agrees swaps in silently", async () => {
    let land: (reply: Render) => void = () => undefined;
    vi.mocked(renderStrips).mockImplementation(() => new Promise<Render>((resolve) => (land = resolve)));
    const screen = await draw();
    await expect.element(screen.getByRole("button", { name: WORDS })).toBeInTheDocument();
    const settles = () => screen.container.querySelectorAll<HTMLElement>("[data-settle]");
    edit(editor, span.id, null);
    typeInto(editor, "is here");
    finish(editor, true);
    // The live check found every letter; the server lacks one.
    land(aReply({ fits: { [span.id]: aFit({ delta_pt: -10, missing: ["h"] }) } }));
    await expect.poll(() => settles().length).toBe(1);
    expect(getComputedStyle(settles()[0] as HTMLElement).animationDuration).toBe("0.4s");

    edit(editor, span.id, null);
    typeInto(editor, "as here");
    finish(editor, true);
    land(aReply({ fits: { [span.id]: aFit({ delta_pt: -5 }) } }));
    await expect.poll(() => editor.store.get().drawn.fits.get(0)?.[span.id]?.delta_pt).toBe(-5);
    expect(settles()).toHaveLength(0);
  });

  test("a preview sets its words on the span's own line, so they don't move when the render lands", async () => {
    const screen = await draw();
    await expect.element(screen.getByRole("button", { name: WORDS })).toBeInTheDocument();
    edit(editor, span.id, null);
    typeInto(editor, "is here");
    finish(editor, true);
    const preview = () => screen.container.querySelector<HTMLElement>("[data-preview]");
    await expect.poll(() => preview()?.textContent).toBe("is here");
    // A line as tall as the span's box centres the words in it, as the field does.
    const style = getComputedStyle(preview() as HTMLElement);
    expect(parseFloat(style.lineHeight)).toBeCloseTo(parseFloat(style.height), 0);
  });

  test("an edit the server drew other than typed keeps the change's solid line, with a warning's dashed one under it", async () => {
    const message = "no h in this font, so the line is drawn in Liberation Serif Regular";
    vi.mocked(renderStrips).mockResolvedValue(aReply({ fits: { [span.id]: aFit({ missing: ["h"], message }) } }));
    const screen = await draw();
    await expect.element(screen.getByRole("button", { name: WORDS })).toBeInTheDocument();
    edit(editor, span.id, null);
    typeInto(editor, "is here");
    finish(editor, true);
    const mark = screen.getByRole("button", { name: "is here" });
    await expect.poll(() => editor.store.get().drawn.fits.get(0)?.[span.id]?.missing).toEqual(["h"]);
    const changed = getComputedStyle(mark.element(), "::after");
    expect(changed.backgroundImage).toBe("none");
    expect(changed.height).toBe("2px");
    const warned = getComputedStyle(mark.element(), "::before");
    expect(warned.backgroundImage).toContain("repeating-linear-gradient");
    expect(warned.height).toBe("2px");
  });

  test("find tints only the words it found, not their whole line", async () => {
    const screen = await draw();
    await expect.element(screen.getByRole("button", { name: WORDS })).toBeInTheDocument();
    openFind(editor);
    typeQuery(editor, "here");
    const found = () => screen.container.querySelector<HTMLElement>("[data-found]");
    await expect.poll(() => found()).not.toBeNull();
    // "was here" is eight 10 pt letters from 72 pt: "here" is its last four, from 112 pt to 152 pt.
    expect(parseFloat(found()?.style.left ?? "")).toBeCloseTo((112 / A4.width) * 100, 3);
    expect(parseFloat(found()?.style.width ?? "")).toBeCloseTo((40 / A4.width) * 100, 3);
  });

  describe("undo, before the server's render lands", () => {
    const below = aSpan({ id: "below", text: WORDS, size: 20, bbox: { x0: 72, y0: 300, x1: 152, y1: 324 } });
    const beside = aSpan({ id: "beside", text: WORDS, size: 20, bbox: { x0: 300, y0: 100, x1: 380, y1: 124 } });
    const strip = (y: number): ImageInfo => ({ page: 0, y, image: "" });

    /** Both spans changed, and the server's strips for them in. */
    async function bothChanged(other: typeof below, images: ImageInfo[]) {
      openDocument(aDoc({ spans: [span, other], fonts: DOC.fonts }));
      vi.mocked(renderStrips).mockResolvedValue(aReply({ images }));
      const screen = await draw();
      await expect.element(screen.getByRole("button", { name: WORDS }).first()).toBeInTheDocument();
      for (const id of [span.id, other.id]) {
        edit(editor, id, null);
        typeInto(editor, "is here");
        finish(editor, true);
      }
      await expect.poll(() => screen.container.querySelectorAll("img[data-strip]").length).toBe(images.length);
      vi.mocked(renderStrips).mockReturnValue(new Promise<Render>(() => undefined));
      return screen;
    }

    test("takes off the strip of a line no other change is on: the page beneath is right as it is", async () => {
      const screen = await bothChanged(below, [strip(98), strip(298)]);
      undo(editor);
      await expect.poll(() => screen.container.querySelectorAll("img[data-strip]").length).toBe(1);
      expect(screen.container.querySelector("[data-preview]")).toBeNull();
    });

    test("puts the page's own pixels over a span put back on a line still changed, never its words in another face", async () => {
      const screen = await bothChanged(beside, [strip(98)]);
      undo(editor);
      await expect.poll(() => screen.container.querySelector("[data-original]")).not.toBeNull();
      expect(screen.container.querySelectorAll("img[data-strip]")).toHaveLength(1);
      expect(screen.container.querySelector("[data-preview]")).toBeNull();
    });
  });

  test("a page whose image fails says why on the page, in the server's words", async () => {
    const busy = "We can't work on files right now. Try again in a few minutes.";
    // No server answers in this test, so the page's image fails as it would with one too busy.
    vi.mocked(pageImage).mockRejectedValueOnce(new ProblemError(aProblem(503, busy)));
    const screen = await draw();
    await expect.element(screen.getByRole("region", { name: "Page 1" }).getByText(busy)).toBeVisible();
  });

  test("a page wider than A4 shows at its printed size too", async () => {
    await page.viewport(1400, 900);
    const letter: PageInfo = { width: 612, height: 792, turn_cw: 0 };
    const screen = await draw(0, letter);
    const sheet = sheetOf(screen.getByRole("region", { name: "Page 1" }).element());
    // 612 pt at 96 dpi is 816 px.
    expect(sheet.getBoundingClientRect().width).toBeCloseTo(816, 0);
  });

  test("on a wide screen, a margin note shows only the words the edit changed, and its name the whole line", async () => {
    await page.viewport(1400, 900);
    const screen = await draw();
    await editSpan(screen);
    await screen.getByRole("textbox").fill("is here");
    await userEvent.keyboard("{Enter}");
    const note = screen.getByRole("button", { name: `Undo: “is here” goes back to “${WORDS}”` });
    await expect.element(note).toBeVisible();
    expect(note.element().textContent).toBe("was");
  });

  test("on a narrow screen, each change is a line under the page that says it's pressed to undo", async () => {
    await page.viewport(400, 900);
    const screen = await draw();
    await editSpan(screen);
    await screen.getByRole("textbox").fill("is here");
    await userEvent.keyboard("{Enter}");
    const line = screen.getByRole("button", { name: `Undo: “is here” goes back to “${WORDS}”` });
    await expect.element(line).toBeVisible();
    expect(line.element().textContent).toBe(`${WORDS}is hereUndo`);
  });

  test("a span with a note shares the page's note: keyboard focus shows it, Escape closes it, Enter still edits", async () => {
    const field = aSpan({ id: "s3", text: "SSN 078", form_field: true, bbox: { x0: 72, y0: 300, x1: 152, y1: 324 } });
    openDocument(aDoc({ spans: [field], fonts: [aFont("Times-Roman")] }));
    const screen = await draw();
    const mark = screen.getByRole("button", { name: "SSN 078" });
    await expect.element(mark).toBeInTheDocument();
    // Focus from the keyboard: a key pressed first, as Tab would be.
    await userEvent.keyboard("{Shift}");
    mark.element().focus();
    const note = screen.getByText(COPY.form_field_not_edited, { exact: false });
    await expect.element(note).toBeVisible();
    await expect.element(mark).toHaveAccessibleDescription(COPY.form_field_not_edited);

    await userEvent.keyboard("{Escape}");
    await expect.element(note).not.toBeInTheDocument();
    await userEvent.keyboard("{Enter}");
    await expect.element(screen.getByRole("textbox", { name: "Change “SSN 078”" })).toHaveFocus();
  });

  test("untouched, a span in a similar font has no line; editing it warns in full until its font is quieted", async () => {
    const substituted = aSpan({
      id: "s3",
      text: "in arial",
      font: "Arial",
      bbox: { x0: 72, y0: 300, x1: 152, y1: 324 },
      fidelity: "substitute",
      substitute: "Liberation Sans Regular",
    });
    const substitutedFont = aFont("Arial", { substitute: "Liberation Sans Regular", why: "Not embedded.", glyphs });
    openDocument(aDoc({ spans: [substituted], fonts: [substitutedFont] }));
    const screen = await draw();
    const mark = screen.getByRole("button", { name: "in arial" });
    await expect.element(mark).toBeInTheDocument();
    expect(getComputedStyle(mark.element(), "::after").backgroundImage).toBe("none");
    // That hover opens no note is `lookOf`'s to hold (marks.test.ts): an absence can't be waited for.
    const note = screen.getByText("Edits here use Liberation Sans Regular", { exact: false });
    mark.element().focus();
    await userEvent.keyboard("{Enter}");
    await expect.element(note).toBeVisible();
    await expect.element(screen.getByText("Not embedded.")).toBeVisible();
    await screen.getByRole("button", { name: "Don't show again for this font" }).click();
    await expect.element(note).not.toBeInTheDocument();
    // Quieted, the chip still gives its short warning (Rule 1).
    await expect.element(screen.getByText("a similar font", { exact: true })).toBeVisible();
    await userEvent.keyboard("{Escape}");
    await userEvent.keyboard("{Enter}");
    await expect.element(screen.getByRole("textbox", { name: "Change “in arial”" })).toHaveFocus();
    await expect.element(note).not.toBeInTheDocument();
  });

  test("text a form field draws has the dashed warning line, and its note says an edit is left out", async () => {
    const field = aSpan({ id: "s3", text: "SSN 078", form_field: true, bbox: { x0: 72, y0: 300, x1: 152, y1: 324 } });
    openDocument(aDoc({ spans: [field], fonts: [aFont("Times-Roman")] }));
    const screen = await draw();
    const mark = screen.getByRole("button", { name: "SSN 078" });
    await expect.element(mark).toBeInTheDocument();
    const line = getComputedStyle(mark.element(), "::after");
    expect(line.backgroundImage).toContain("repeating-linear-gradient");
    // At rest, before anything is touched, it's the Rule 1 signal: a mark's 3:1 on the white page.
    expect(contrastOnWhite(line)).toBeGreaterThanOrEqual(3);
    await mark.hover();
    await expect.element(screen.getByText(COPY.form_field_not_edited)).toBeVisible();
  });

  test("a font quieted in one Type3 copy is quiet in its others: the chip names them as one", async () => {
    const turned = { fidelity: "approximate", why: { code: "turned_text", params: {} } } as const;
    const copyOf = (id: string, font: string, y0: number) =>
      aSpan({ id, text: "in drawn", font, bbox: { x0: 72, y0, x1: 152, y1: y0 + 24 }, ...turned });
    const fontOf = (name: string) => aFont(name, { shown_name: "Drawn-Regular", glyphs });
    openDocument(
      aDoc({
        spans: [copyOf("s3", "Type3 (5 0 R)", 300), copyOf("s4", "Type3 (9 0 R)", 400)],
        fonts: [fontOf("Type3 (5 0 R)"), fontOf("Type3 (9 0 R)")],
      }),
    );
    const screen = await draw();
    const marks = screen.getByRole("button", { name: "in drawn" });
    await expect.element(marks.nth(0)).toBeInTheDocument();
    marks.nth(0).element().focus();
    await userEvent.keyboard("{Enter}");
    // The file's own font draws it, named as the reader knows it, not by its object.
    await expect.element(screen.getByText("Drawn-Regular", { exact: true })).toBeVisible();
    await screen.getByRole("button", { name: "Don't show again for this font" }).click();
    await userEvent.keyboard("{Escape}");

    marks.nth(1).element().focus();
    await userEvent.keyboard("{Enter}");
    await expect.element(screen.getByRole("textbox", { name: "Change “in drawn”" })).toHaveFocus();
    await expect.element(screen.getByText(COPY.approximate.turned_text ?? "")).not.toBeInTheDocument();
    await expect.element(screen.getByText("will look different", { exact: true })).toBeVisible();
  });

  test("text an edit won't match has no line at rest; editing it says why, warned, and the chip that it will look different", async () => {
    const turned = aSpan({
      id: "s4",
      text: "turned",
      fidelity: "approximate",
      why: { code: "turned_text", params: {} },
      bbox: { x0: 72, y0: 300, x1: 152, y1: 324 },
    });
    openDocument(aDoc({ spans: [turned], fonts: [aFont("Times-Roman", { glyphs })] }));
    const screen = await draw();
    const mark = screen.getByRole("button", { name: "turned" });
    await expect.element(mark).toBeInTheDocument();
    expect(getComputedStyle(mark.element(), "::after").backgroundImage).toBe("none");
    mark.element().focus();
    await userEvent.keyboard("{Enter}");
    const note = screen.getByText(COPY.approximate.turned_text ?? "");
    await expect.element(note).toBeVisible();
    await expect.element(screen.getByText("will look different", { exact: true })).toBeVisible();
    expect(getComputedStyle(note.element()).color).toBe(colorOf("--warn"));
  });

  test("a click shows a span's note; a click's focus alone doesn't", async () => {
    const field = aSpan({ id: "s3", text: "SSN 078", form_field: true, bbox: { x0: 72, y0: 300, x1: 152, y1: 324 } });
    openDocument(aDoc({ spans: [field], fonts: [aFont("Times-Roman")] }));
    const screen = await draw();
    const mark = screen.getByRole("button", { name: "SSN 078" });
    await expect.element(mark).toBeInTheDocument();
    const note = screen.getByText(COPY.form_field_not_edited);
    // A pointer was used last: focus that comes without a press is a click's, not the keyboard's.
    await userEvent.click(screen.getByRole("region", { name: "Page 1" }), { position: { x: 1, y: 1 } });
    mark.element().focus();
    // Focus is taken: the mark points at the page's note, which a click's focus leaves shut.
    await expect.element(mark).toHaveAttribute("aria-describedby");
    await expect.element(note).not.toBeInTheDocument();
    await mark.click();
    await expect.element(note).toBeVisible();
  });
});
