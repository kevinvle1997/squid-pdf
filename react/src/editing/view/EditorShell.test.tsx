import { afterEach, beforeAll, beforeEach, describe, expect, test, vi } from "vitest";
import { cdp, page, userEvent } from "vitest/browser";
import { render } from "vitest-browser-react";
import "../../styles/tokens.css";
import "../../styles/base.css";
import SOME_FONT from "../../../../src/squidpdf/fonts/LiberationSans-Regular.ttf?url";
import {
  documentOf,
  exportPdf,
  fontList,
  ProblemError,
  putFont,
  renderStrips,
  stillThere,
  upload,
} from "../../api/client";
import type { Document, Render } from "../../api/types";
import { type PersistedWork, persistFile, persistState } from "../../documents/localPersistence";
import {
  A4,
  aDoc,
  aDocumentNotice,
  aFit,
  aFont,
  aFontList,
  aProblem,
  aReplace,
  aReply,
  aSkipped,
  aSpan,
  aSpanNotice,
  halfWideGlyphs,
  SENTENCES,
} from "../../fixtures";
import { colorOf, emulateTouchScreen } from "../../testing";
import { EXPORT_INTENT_MS, LINE_BELOW_EM, NEW_TEXT_DESCENT_EM, NOTE_CLOSE_DELAY_MS } from "../constants";
import { learnDateLanguages } from "../dateNames";
import { EMPTY_HISTORY, historyReducer } from "../history";
import { fill } from "../words";
import { EditorShell } from "./EditorShell";

// What's persisted in this browser is the App's to test, in the browser's real storage; here, what's asked of it.
vi.mock(import("../../documents/localPersistence"), async (original) => ({
  ...(await original()),
  persistFile: vi.fn(async () => undefined),
  persistState: vi.fn(async () => undefined),
}));

vi.mock(import("../../api/client"), async (original) => ({
  ...(await original()),
  renderStrips: vi.fn(),
  stillThere: vi.fn(async () => true),
  pageImage: vi.fn(async () => undefined),
  putFont: vi.fn(),
  exportPdf: vi.fn(),
  upload: vi.fn(),
  documentOf: vi.fn(),
  fontList: vi.fn(),
}));

const span = aSpan({ id: "s1", text: "was here", size: 20, bbox: { x0: 72, y0: 100, x1: 152, y1: 124 } });
const DOC = aDoc({ spans: [span], fonts: [aFont("Times-Roman")] });

// Arial only named: a similar font stands in, and its letters are the ones the page lists.
const LETTERS = halfWideGlyphs("abcdefghijklmnopqrstuvwxyz ");
const NAMED = aFont("Arial", { substitute: "Liberation Sans Regular", glyphs: LETTERS });
// Arial is only named in the file: a similar font draws any edit of it.
const ARIAL_SPAN = aSpan({
  ...span,
  font: "Arial",
  fidelity: "substitute",
  substitute: "Liberation Sans Regular",
  in_file: false,
});
const ARIAL_DOC = aDoc({ spans: [ARIAL_SPAN], fonts: [NAMED] });
const USERS_COPY = aDoc({
  spans: [{ ...ARIAL_SPAN, fidelity: "exact", substitute: null }],
  fonts: [aFont("Arial", { attached: true, glyphs: { ...LETTERS, Y: 667 } })],
});
const FONT_FILE = new File(["a font"], "arial.ttf", { type: "font/ttf" });
const MISMATCH = "This isn't the font the document uses. Its letters are a different width.";

beforeEach(() => {
  // The server's render never answers unless a test gives it a reply.
  vi.mocked(renderStrips)
    .mockReset()
    .mockReturnValue(new Promise<Render>(() => undefined));
  vi.mocked(putFont).mockReset();
  vi.mocked(fontList).mockReset().mockResolvedValue(aFontList());
});

/** The editor on `doc`, opened from a file named `name`, restoring `persisted` when given. */
function openEditor({
  doc = DOC,
  name = "contract.pdf",
  onOpen = vi.fn(),
  persisted,
}: {
  doc?: Document;
  name?: string;
  onOpen?: (file: File) => void;
  persisted?: PersistedWork<unknown>;
} = {}) {
  return render(
    <EditorShell opened={{ file: new File(["%PDF-"], name), doc }} onOpen={onOpen} persisted={persisted} />,
  );
}

/** Open the field on "was here" and type `text` in it. */
async function typeInSpan(screen: Awaited<ReturnType<typeof render>>, text: string) {
  const mark = screen.getByRole("button", { name: "was here" });
  await expect.element(mark).toBeInTheDocument();
  mark.element().focus();
  await userEvent.keyboard("{Enter}");
  await screen.getByRole("textbox").fill(text);
}

/** Change "was here" to `text`, kept with Enter. */
async function changeSpan(screen: Awaited<ReturnType<typeof render>>, text: string) {
  await typeInSpan(screen, text);
  await userEvent.keyboard("{Enter}");
}

describe("the editor", () => {
  test("what the server's render says it did other than asked is shown under the bar, and heard", async () => {
    const drewOtherwise = "This page wouldn't take Times, so the line is drawn in Liberation Serif.";
    const leftOut = "An edit points at text that isn't in this document, so it was left out.";
    vi.mocked(renderStrips).mockResolvedValue(
      aReply({ notices: [aSpanNotice(drewOtherwise, span.id)], skipped: [aSkipped(0, leftOut)] }),
    );
    const screen = await openEditor();
    await changeSpan(screen, "is here");
    await expect.element(screen.getByText(drewOtherwise, { exact: true }).first()).toBeVisible();
    await expect.element(screen.getByText(leftOut, { exact: true }).first()).toBeVisible();
    await expect.element(screen.getByRole("status")).toHaveTextContent(`${drewOtherwise} ${leftOut}`);
  });

  test("every notice the document opened with is shown, not only the first, and heard", async () => {
    const notices = [
      aDocumentNotice("Some text is in a font we can't use.", "a"),
      aDocumentNotice("This file is signed; editing breaks the signature.", "b"),
    ];
    const screen = await openEditor({ doc: { ...DOC, notices } });
    const [first, second] = notices.map((notice) => notice.detail);
    await expect.element(screen.getByText(first ?? "", { exact: true }).first()).toBeVisible();
    await expect.element(screen.getByText(second ?? "", { exact: true }).first()).toBeVisible();
    await expect.element(screen.getByRole("status")).toHaveTextContent(`${first} ${second}`);
  });

  test("a signed document says so under the bar as it opens, heard once, and it stays there past the first edit", async () => {
    const said = "This PDF is signed. Any change breaks the signature, and so does downloading it from here.";
    // Its font has every letter typed, so the signature's sentence is all the field hears.
    const fonts = [aFont("Times-Roman", { glyphs: LETTERS })];
    const signed = aDoc({ spans: [span], fonts, signed: true, notices: [aDocumentNotice(said, "signed_file")] });
    const screen = await openEditor({ doc: signed });
    const line = screen.getByText(said, { exact: true }).first();
    await expect.element(line).toBeVisible();
    const underTheBar = screen.getByRole("banner").element().compareDocumentPosition(line.element());
    expect(underTheBar & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(line.element().closest("main")).toBeNull();
    await expect.element(screen.getByRole("status")).toHaveTextContent(said);
    const mark = screen.getByRole("button", { name: "was here" });
    mark.element().focus();
    await userEvent.keyboard("{Enter}");
    await expect.element(screen.getByRole("status")).toHaveTextContent(SENTENCES.edit_breaks_signature);
    await screen.getByRole("textbox").fill("is here");
    await userEvent.keyboard("{Enter}");
    await expect.element(screen.getByRole("status")).toHaveTextContent(fill(SENTENCES.changed_to, { text: "is here" }));
    await expect.element(screen.getByRole("status")).not.toHaveTextContent(said);
    await expect.element(line).toBeVisible();
  });

  test("a signed document persisted before it said so, once read again, says so under the bar, heard once", async () => {
    const said = "This PDF is signed. Any change breaks the signature, and so does downloading it from here.";
    const signed = aDoc({ spans: [span], signed: true, notices: [aDocumentNotice(said, "signed_file")] });
    // Persisted by an earlier build: no word of a signature, and no sentence for one.
    const stale = { ...signed, notices: [], sentences: { ...SENTENCES } };
    Reflect.deleteProperty(stale, "signed");
    Reflect.deleteProperty(stale.sentences, "edit_breaks_signature");
    vi.mocked(documentOf).mockReset().mockResolvedValue(signed);
    const persisted = { work: { history: EMPTY_HISTORY, fonts: [] }, until: Date.now() + 60_000 };
    const screen = await openEditor({ doc: stale, persisted });
    await expect.element(screen.getByText(said, { exact: true }).first()).toBeVisible();
    await expect.element(screen.getByRole("status")).toHaveTextContent(said);
    expect(screen.getByRole("status").element().textContent).toBe(said);
  });
});

describe("finding your way", () => {
  const twice = aDoc({
    spans: [span, aSpan({ id: "s2", text: "here too", bbox: { x0: 72, y0: 140, x1: 152, y1: 164 } })],
    fonts: [aFont("Times-Roman")],
  });

  test("in find, Enter goes to the next place found and Shift+Enter the one before, round, each announced", async () => {
    const screen = await openEditor({ doc: twice });
    await userEvent.keyboard("{Control>}f{/Control}");
    await userEvent.keyboard("here");
    const bar = screen.getByRole("search");
    await expect.element(bar.getByText("2 places will look identical")).toBeVisible();
    await userEvent.keyboard("{Enter}");
    await expect.element(bar.getByText("1 of 2")).toBeVisible();
    await expect.element(screen.getByRole("status")).toHaveTextContent("1 of 2");
    await userEvent.keyboard("{Enter}");
    await expect.element(bar.getByText("2 of 2")).toBeVisible();
    await userEvent.keyboard("{Enter}");
    await expect.element(bar.getByText("1 of 2")).toBeVisible();
    await userEvent.keyboard("{Shift>}{Enter}{/Shift}");
    await expect.element(bar.getByText("2 of 2")).toBeVisible();
    await bar.getByRole("button", { name: "Previous place" }).click();
    await expect.element(bar.getByText("1 of 2")).toBeVisible();
    // The place gone to is ringed, apart from the rest found.
    const ringed = [...document.querySelectorAll("[data-found]")].filter(
      (found) => getComputedStyle(found).outlineStyle === "solid",
    );
    expect(ringed).toHaveLength(1);
  });

  test("the bar says which page is being read", async () => {
    const pages = [A4, A4];
    const screen = await openEditor({ doc: { ...twice, pages } });
    await expect.element(screen.getByRole("banner").getByText("Page 1 of 2")).toBeVisible();
  });

  test("zoomed in, the page shows wider than printed; pressing the share shown fits it to the window again", async () => {
    const [wide, tall] = [window.innerWidth, window.innerHeight];
    await page.viewport(1400, 900);
    try {
      const screen = await openEditor();
      const sheet = () =>
        screen.getByRole("region", { name: "Page 1" }).element().querySelector("[style*='aspect-ratio']");
      const printed = (DOC.pages[0]?.width ?? 0) * (4 / 3);
      await expect.element(screen.getByRole("button", { name: "Fit to the window, now 100%" })).toBeVisible();
      await screen.getByRole("button", { name: "Zoom in" }).click();
      await expect.poll(() => sheet()?.getBoundingClientRect().width).toBeCloseTo(printed * 1.25, 0);
      await expect.element(screen.getByRole("button", { name: "Fit to the window, now 125%" })).toBeVisible();
      await screen.getByRole("button", { name: "Fit to the window, now 125%" }).click();
      await expect.poll(() => sheet()?.getBoundingClientRect().width).toBeCloseTo(printed, 0);
    } finally {
      await page.viewport(wide, tall);
    }
  });

  test.each([
    ["at its printed size, from the keyboard", 0],
    ["zoomed past the window and scrolled to its far side", 3],
  ])("on a phone, a receipt's slip opens its calendar inside the window, the gutter in, %s", async (_, zoomIns) => {
    const [wide, tall] = [window.innerWidth, window.innerHeight];
    await page.viewport(390, 844);
    try {
      await emulateTouchScreen();
      await learnDateLanguages();
      const words = "Due 14 March 2026";
      const due = aSpan({ id: "due", text: words, size: 10, bbox: { x0: 20, y0: 20, x1: 110, y1: 32 } });
      const glyphs = halfWideGlyphs(`${words}0123456789`);
      const receipt = { width: 200, height: 120, turn_cw: 0 } as const;
      const screen = await openEditor({
        doc: aDoc({ pages: [receipt], spans: [due], fonts: [aFont("Times-Roman", { glyphs })] }),
      });
      const mark = screen.getByRole("button", { name: words });
      await expect.element(mark).toBeInTheDocument();
      for (let times = 0; times < zoomIns; times++) await screen.getByRole("button", { name: "Zoom in" }).click();
      const pages = document.getElementById("pages");
      if (pages === null) throw new Error("no pages");
      await expect.poll(() => zoomIns === 0 || pages.scrollWidth > pages.clientWidth).toBe(true);
      pages.scrollLeft = pages.scrollWidth;

      mark.element().focus();
      await userEvent.keyboard("{Enter}");
      const control = screen.getByRole("button", { name: "Date" });
      await expect.element(control).toBeVisible();
      await userEvent.keyboard("{Alt>}{ArrowDown}{/Alt}");
      await expect.element(control).toHaveAttribute("aria-expanded", "true");
      await new Promise(requestAnimationFrame);
      await new Promise(requestAnimationFrame);
      await Promise.all(document.getAnimations().map((animation) => animation.finished));
      const slip = control.element().closest("[data-slip]")?.getBoundingClientRect() ?? new DOMRect();
      expect(slip.left).toBeGreaterThanOrEqual(16 - 1);
      expect(slip.right).toBeLessThanOrEqual(390 - 16 + 1);
    } finally {
      await page.viewport(wide, tall);
    }
  });

  test("? lists every key the editor answers to, and Escape shuts the list", async () => {
    const screen = await openEditor();
    await expect.element(screen.getByRole("button", { name: "was here" })).toBeInTheDocument();
    await userEvent.keyboard("?");
    const list = screen.getByRole("dialog", { name: "Keyboard shortcuts" });
    await expect.element(list).toBeVisible();
    await expect.element(list.getByText("The next place found, or the one before")).toBeVisible();
    await userEvent.keyboard("{Escape}");
    await expect.element(list).not.toBeInTheDocument();
  });

  test("in an open calendar, undo and ? are the edit's, not the window's: nothing else is undone, no list opens", async () => {
    const due = aSpan({ id: "due", text: "Due 14 March 2026", bbox: { x0: 72, y0: 200, x1: 242, y1: 224 }, size: 20 });
    const glyphs = halfWideGlyphs("abcdefghijklmnopqrstuvwxyzDM0123456789 ");
    const doc = aDoc({ spans: [span, due], fonts: [aFont("Times-Roman", { glyphs })] });
    const screen = await openEditor({ doc });
    await changeSpan(screen, "is here");
    const mark = screen.getByRole("button", { name: "Due 14 March 2026" });
    await expect.element(mark).toBeInTheDocument();
    mark.element().focus();
    await userEvent.keyboard("{Enter}");
    await userEvent.keyboard("{Alt>}{ArrowDown}{/Alt}");
    await expect.element(screen.getByRole("button", { name: /March 14, 2026/ })).toHaveFocus();

    await userEvent.keyboard("{Control>}z{/Control}");
    await userEvent.keyboard("?");
    await expect.element(screen.getByRole("button", { name: /March 14, 2026/ })).toHaveFocus();
    expect(screen.getByRole("button", { name: "is here" }).query()).not.toBeNull();
    expect(screen.getByRole("dialog", { name: "Keyboard shortcuts" }).query()).toBeNull();
  });

  test("the tab names the document, starred while it has changes no export took", async () => {
    const screen = await openEditor();
    await expect.poll(() => document.title).toBe("contract.pdf · squid-pdf");
    await changeSpan(screen, "is here");
    await expect.poll(() => document.title).toBe("* contract.pdf · squid-pdf");
  });
});

describe("the bar", () => {
  test("says when the document couldn't be opened again, where the document's name is", async () => {
    const unreachable = "We can't work on files right now. Try again in a few minutes.";
    vi.mocked(stillThere).mockRejectedValueOnce(new ProblemError(aProblem(503, unreachable)));
    const screen = await openEditor();
    // The page image fails to load here, as it does once the server has lost the document.
    await expect.element(screen.getByRole("banner").getByText(unreachable)).toBeVisible();
    await expect.element(screen.getByRole("status")).toHaveTextContent(unreachable);
    // Shown once, in the bar: the status only says it.
    const shown = screen
      .getByText(unreachable)
      .elements()
      .filter((element) => !element.closest("[role=status]"));
    expect(shown).toHaveLength(1);
    // The sentence wraps; the bar's buttons keep their one line.
    const [wide, tall] = [window.innerWidth, window.innerHeight];
    await page.viewport(900, 700);
    const exportButton = screen.getByRole("button", { name: /Export/ }).element();
    expect(exportButton.getBoundingClientRect().height).toBeLessThan(40);
    await page.viewport(wide, tall);
  });

  test("before any change, it counts the places an edit will use a similar font, warned; then the changes", async () => {
    const screen = await openEditor({ doc: ARIAL_DOC });
    const bar = screen.getByRole("banner");
    await expect.element(bar.getByText("will use a similar font", { exact: false })).toBeVisible();
    expect(bar.getByText("will use a similar font", { exact: false }).element().textContent).toBe(
      "Edits in 1 place will use a similar font",
    );
    // The bar's own warning colour, which reads on its ink, on the whole sentence, as Find's count marks its own.
    const warn = bar.element().appendChild(document.createElement("span"));
    warn.style.color = "var(--warn)";
    const warned = bar.getByText("Edits in 1 place will use a similar font", { exact: true });
    expect(getComputedStyle(warned.element()).color).toBe(getComputedStyle(warn).color);
    warn.remove();
    await changeSpan(screen, "was there");
    await expect.element(bar.getByText("1 change", { exact: false })).toBeVisible();
    await expect.element(bar.getByText("will use a similar font", { exact: false })).not.toBeInTheDocument();
  });

  test("after a page's delete, it still counts the places an edit will use a similar font; after a word's change, the changes", async () => {
    const pageTwo = { ...ARIAL_SPAN, id: "s2", page: 1 };
    const screen = await openEditor({ doc: aDoc({ spans: [ARIAL_SPAN, pageTwo], fonts: [NAMED], pages: [A4, A4] }) });
    const bar = screen.getByRole("banner");
    const forecast = bar.getByRole("button", { name: "Edits in 2 places will use a similar font" });
    await expect.element(forecast).toBeVisible();
    await screen.getByRole("button", { name: "Delete page 2", exact: true }).click();
    await expect.element(forecast).toBeVisible();
    await changeSpan(screen, "was there");
    // The delete counts among the changes.
    await expect.element(bar.getByText("2 changes", { exact: false })).toBeVisible();
    await expect.element(forecast).not.toBeInTheDocument();
  });

  test("the count opens where an edit won't match, grouped by font; Escape shuts it, focus back on the count", async () => {
    const screen = await openEditor({ doc: ARIAL_DOC });
    const count = screen.getByRole("button", { name: "Edits in 1 place will use a similar font" });
    await count.click();
    const list = screen.getByRole("dialog", { name: "Where edits won't match" });
    await expect.element(list.getByRole("heading", { name: "In a similar font" })).toBeVisible();
    await expect.element(list.getByText("Arial", { exact: true })).toBeVisible();
    await expect.element(list.getByText("1 place", { exact: true })).toBeVisible();
    await userEvent.keyboard("{Escape}");
    await expect.element(list).not.toBeInTheDocument();
    await expect.element(count).toHaveFocus();

    // After a change, the warned part of the count opens the changes' own.
    await changeSpan(screen, "was there");
    await screen.getByRole("button", { name: /in a similar font$/ }).click();
    await expect.element(screen.getByRole("dialog").getByText("Arial", { exact: true })).toBeVisible();
  });

  test("after a change, it counts the lines left long past their room in the server's sentence, warned whole", async () => {
    const tooLong = { code: "too_long", params: { percent: 12 } };
    vi.mocked(renderStrips).mockResolvedValue(
      aReply({ fits: { [span.id]: aFit({ delta_pt: 10, message: "about 12% too long", message_parts: [tooLong] }) } }),
    );
    const screen = await openEditor();
    await changeSpan(screen, "is here and there");
    const long = screen.getByRole("banner").getByText("1 too long for its place", { exact: true });
    await expect.element(long).toBeVisible();
    expect(getComputedStyle(long.element()).color).toBe(colorOf("--bar-warn"));
  });

  test("a row's eye marks its places on the page, shutting the list that would lie over them, until it's pressed again", async () => {
    const screen = await openEditor({ doc: ARIAL_DOC });
    await screen.getByRole("button", { name: "Edits in 1 place will use a similar font" }).click();
    const eye = screen.getByRole("button", { name: "Show the places in Arial on the page" });
    const marked = () => document.querySelectorAll(`[data-shown]`).length;
    expect(marked()).toBe(0);

    await eye.click();
    await expect.element(screen.getByRole("dialog")).not.toBeInTheDocument();
    await expect.poll(marked).toBe(1);
    // The count says its places are shown, and opens the list again to stop showing them.
    const count = screen.getByRole("button", { name: "Edits in 1 place will use a similar font (shown on the page)" });
    await count.click();
    const shown = screen.getByRole("button", { name: "Show the places in Arial on the page" });
    await expect.element(shown).toHaveAttribute("aria-pressed", "true");
    await shown.click();
    await expect.poll(marked).toBe(0);
    await expect.element(screen.getByRole("dialog")).toBeInTheDocument();
  });

  test("on a touch screen, no button hints at a key", async () => {
    await emulateTouchScreen();
    const screen = await openEditor();
    const key = screen
      .getByRole("button", { name: /^Export/ })
      .element()
      .querySelector("kbd");
    expect(key === null ? "none" : getComputedStyle(key).display).toBe("none");
  });
});

describe("export", () => {
  test("says it's under way only past the delay, so a quick one shows nothing", async () => {
    vi.mocked(exportPdf).mockReturnValue(new Promise(() => undefined));
    const screen = await openEditor();
    await screen.getByRole("button", { name: /Export/ }).click();
    const exporting = screen.getByRole("banner").getByText("Exporting");
    await expect.element(exporting).toBeInTheDocument();
    await expect.element(exporting).not.toBeVisible();
    await expect.element(exporting).toBeVisible();
  });
});

describe("export, started on the way to pressing it", () => {
  const PDF = { pdf: new Blob(["%PDF-"], { type: "application/pdf" }), skipped: [], notices: [] };

  beforeEach(() => {
    vi.mocked(exportPdf).mockReset().mockResolvedValue(PDF);
  });

  /**
   * The editor, the pointer moved onto the file's name first: a test before may have left it where Export is
   * drawn now, and Chrome hovers what's drawn under a still pointer when it likes.
   */
  async function drawn() {
    const screen = await openEditor();
    await screen.getByText("contract.pdf").hover();
    return screen;
  }

  test("starts once the pointer rests on Export, and the press takes it: one export, downloaded", async () => {
    const screen = await drawn();
    await changeSpan(screen, "is here");
    const exportButton = screen.getByRole("button", { name: /^Export/ });
    await exportButton.hover();
    await vi.waitFor(() => expect(exportPdf).toHaveBeenCalledTimes(1));
    expect(vi.mocked(exportPdf).mock.calls[0]?.[1]).toEqual([aReplace(span.id, "is here")]);

    await exportButton.click();
    await expect.element(screen.getByText("Downloaded contract, edited.pdf.").first()).toBeVisible();
    expect(exportPdf).toHaveBeenCalledTimes(1);
  });

  test("the pointer passing over Export starts nothing", async () => {
    const screen = await drawn();
    await changeSpan(screen, "is here");
    // In and out within one task: a test's own pointer moves take longer than passing over does.
    const exportButton = screen.getByRole("button", { name: /^Export/ }).element();
    const pointer = { bubbles: true, pointerType: "mouse" };
    exportButton.dispatchEvent(new PointerEvent("pointerover", { ...pointer, relatedTarget: document.body }));
    exportButton.dispatchEvent(new PointerEvent("pointerout", { ...pointer, relatedTarget: document.body }));
    // Twice as long as the pointer must rest on Export to start one.
    await new Promise((resolve) => setTimeout(resolve, EXPORT_INTENT_MS * 2));
    expect(exportPdf).not.toHaveBeenCalled();
  });

  test("one started before another change isn't taken: the press exports the edits as they are", async () => {
    const screen = await drawn();
    await changeSpan(screen, "is here");
    await screen.getByRole("button", { name: /^Export/ }).hover();
    await vi.waitFor(() => expect(exportPdf).toHaveBeenCalledTimes(1));
    await screen.getByRole("button", { name: "Fonts" }).hover();

    const mark = screen.getByRole("button", { name: "is here" });
    mark.element().focus();
    await userEvent.keyboard("{Enter}");
    await screen.getByRole("textbox").fill("is there");
    await userEvent.keyboard("{Enter}");
    await screen.getByRole("button", { name: /^Export/ }).click();
    await expect.element(screen.getByText("Downloaded contract, edited.pdf.").first()).toBeVisible();
    expect(exportPdf).toHaveBeenCalledTimes(2);
    expect(vi.mocked(exportPdf).mock.calls[1]?.[1]).toEqual([aReplace(span.id, "is there")]);
  });

  test("one that failed is asked for again by the press", async () => {
    vi.mocked(exportPdf).mockRejectedValueOnce(new ProblemError(aProblem(503, "The server is busy.")));
    const screen = await drawn();
    await changeSpan(screen, "is here");
    const exportButton = screen.getByRole("button", { name: /^Export/ });
    await exportButton.hover();
    await vi.waitFor(() => expect(exportPdf).toHaveBeenCalledTimes(1));

    await exportButton.click();
    await expect.element(screen.getByText("Downloaded contract, edited.pdf.").first()).toBeVisible();
    expect(exportPdf).toHaveBeenCalledTimes(2);
  });
});

describe("an emptied span", () => {
  test("the chip says it can't be left empty, and Enter keeps the field open", async () => {
    const screen = await openEditor();
    await changeSpan(screen, "");
    // Shown in the chip, the first on the page, and heard through the status.
    await expect.element(screen.getByText(SENTENCES.empty).first()).toBeVisible();
    await expect.element(screen.getByRole("status")).toHaveTextContent(SENTENCES.empty);
    await expect.element(screen.getByRole("textbox")).toHaveFocus();
    await userEvent.keyboard("{Escape}");
    await expect.element(screen.getByRole("textbox")).not.toBeInTheDocument();
    await expect.element(screen.getByRole("button", { name: "was here" })).toHaveFocus();
  });
});

describe("redaction", () => {
  test("Cmd+Delete redacts the span, and editing it asks first, beside it: Escape keeps it, Edit anyway opens it", async () => {
    const screen = await openEditor();
    const mark = screen.getByRole("button", { name: "was here" });
    await expect.element(mark).toBeInTheDocument();
    mark.element().focus();
    await userEvent.keyboard("{Control>}{Backspace}{/Control}");
    const redacted = screen.getByRole("button", { name: "was here (redacted)" });
    await expect.element(redacted).toHaveFocus();
    await expect.element(screen.getByRole("status")).toHaveTextContent(fill(SENTENCES.redacted, { text: "was here" }));

    await userEvent.keyboard("{Enter}");
    const asked = screen.getByRole("group", { name: SENTENCES.undo_redaction });
    await expect.element(asked).toBeVisible();
    await expect.element(screen.getByRole("button", { name: "Keep redaction" })).toHaveFocus();
    await userEvent.keyboard("{Escape}");
    await expect.element(asked).not.toBeInTheDocument();
    await expect.element(redacted).toHaveFocus();

    await userEvent.keyboard("{Enter}");
    await screen.getByRole("button", { name: "Edit anyway" }).click();
    await expect.element(screen.getByRole("textbox", { name: "Change “was here”" })).toHaveFocus();
  });

  test("in a signed document the bar doesn't say a change breaks the signature while a redaction takes it away", async () => {
    const said = "This PDF is signed. Any change breaks the signature, and so does downloading it from here.";
    const signed = aDoc({ spans: [span], signed: true, notices: [aDocumentNotice(said, "signed_file")] });
    const screen = await openEditor({ doc: signed });
    // The first is the line under the bar; the live region, heard as it opens, comes after.
    const line = screen.getByText(said, { exact: true }).first();
    await expect.element(line).toBeVisible();
    const mark = screen.getByRole("button", { name: "was here" });
    await expect.element(mark).toBeInTheDocument();
    mark.element().focus();
    await userEvent.keyboard("{Control>}{Backspace}{/Control}");
    await expect.element(screen.getByRole("button", { name: "was here (redacted)" })).toHaveFocus();
    await expect.element(line).not.toBeInTheDocument();
    await userEvent.keyboard("{Control>}z{/Control}");
    await expect.element(screen.getByRole("button", { name: "was here" })).toBeInTheDocument();
    await expect.element(line).toBeVisible();
  });

  test("Ctrl+Backspace while typing deletes in the field, as it does anywhere, and redacts nothing", async () => {
    const screen = await openEditor();
    const mark = screen.getByRole("button", { name: "was here" });
    await expect.element(mark).toBeInTheDocument();
    mark.element().focus();
    await userEvent.keyboard("{Enter}{ArrowRight} now");
    await userEvent.keyboard("{Control>}{Backspace}{/Control}");
    await expect.element(screen.getByRole("textbox", { name: "Change “was here”" })).toHaveFocus();
    await expect.element(screen.getByRole("button", { name: "was here (redacted)" })).not.toBeInTheDocument();
  });
});

describe("leaving the page", () => {
  const leave = () => {
    const leaving = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(leaving);
    return leaving.defaultPrevented;
  };

  test("asks first only while edits since the last export aren't persisted in this browser yet", async () => {
    let persisted: () => void = () => undefined;
    vi.mocked(persistState).mockReturnValueOnce(new Promise((resolve) => (persisted = resolve)));
    const screen = await openEditor();
    await expect.element(screen.getByRole("button", { name: "was here" })).toBeInTheDocument();
    expect(leave()).toBe(false);
    await changeSpan(screen, "is here");
    expect(leave()).toBe(true);
    persisted();
    await vi.waitFor(() => expect(leave()).toBe(false));
  });

  test("asks first when this browser keeps nothing, as a private window may", async () => {
    vi.mocked(persistFile).mockRejectedValueOnce(new DOMException("refused", "InvalidStateError"));
    const screen = await openEditor();
    await changeSpan(screen, "is here");
    await vi.waitFor(() => expect(persistFile).toHaveBeenCalled());
    expect(leave()).toBe(true);
  });

  test("work persisted here comes back with the user's copies of its fonts, given again to a server that lost it", async () => {
    vi.mocked(renderStrips).mockRejectedValueOnce(new ProblemError(aProblem(404, "Not found.")));
    vi.mocked(upload).mockResolvedValueOnce({ ...ARIAL_DOC, id: "again" });
    vi.mocked(putFont).mockResolvedValue({ ...USERS_COPY, id: "again" });
    const history = historyReducer(EMPTY_HISTORY, { kind: "add", edits: [aReplace(ARIAL_SPAN.id, "is here")] });
    const fonts = [["Arial", { bytes: await FONT_FILE.arrayBuffer(), type: FONT_FILE.type }]];
    const persisted = { work: { history, fonts }, until: Date.now() + 60_000 };
    const screen = await openEditor({ doc: ARIAL_DOC, name: "letter.pdf", persisted });

    await expect.element(screen.getByRole("button", { name: "is here" })).toBeInTheDocument();
    await vi.waitFor(() => expect(putFont).toHaveBeenCalledWith("again", "Arial", expect.any(Blob)));
    const given = vi.mocked(putFont).mock.calls.at(-1)?.[2];
    expect(await given?.text()).toBe(await FONT_FILE.text());
    expect(given?.type).toBe(FONT_FILE.type);
  });

  test("each change is persisted as it's made: the file once, then the history, until the server's hour ends", async () => {
    vi.mocked(persistFile).mockClear();
    vi.mocked(persistState).mockClear();
    const file = new File(["%PDF-"], "contract.pdf");
    const screen = await render(<EditorShell opened={{ file: file, doc: DOC }} onOpen={vi.fn()} />);
    await changeSpan(screen, "is here");
    await vi.waitFor(() => expect(persistState).toHaveBeenCalledTimes(1));
    expect(persistFile).toHaveBeenCalledWith(file);
    const [persisted] = vi.mocked(persistState).mock.calls[0] ?? [];
    expect(persisted?.until).toBe(Date.parse(DOC.expires_at));
    expect(persisted?.work).toMatchObject({ history: { done: [[{ edit: { span_id: span.id, text: "is here" } }]] } });

    screen.getByRole("button", { name: "is here" }).element().focus();
    await userEvent.keyboard("{Control>}z{/Control}");
    await vi.waitFor(() => expect(persistState).toHaveBeenCalledTimes(2));
    expect(persistFile).toHaveBeenCalledTimes(1);
  });
});

describe("undo and redo", () => {
  test("are heard, the same words again too, and bring their span into view while focus stays", async () => {
    // The span is on the third page, well below the first screen.
    const far = aSpan({ ...span, page: 2 });
    const doc = aDoc({ spans: [far], fonts: [aFont("Times-Roman")], pages: [A4, A4, A4] });
    const screen = await openEditor({ doc });
    screen.getByRole("region", { name: "Page 3" }).element().scrollIntoView();
    await changeSpan(screen, "is here");
    await userEvent.keyboard("{Control>}z{/Control}");
    const status = screen.getByRole("status");
    await expect.element(status).toHaveTextContent(fill(SENTENCES.back_to, { text: "was here" }));
    const firstAnnouncement = status.element().firstElementChild;

    await userEvent.keyboard("{Control>}{Shift>}z{/Shift}{/Control}");
    await expect.element(status).toHaveTextContent(fill(SENTENCES.changed_to, { text: "is here" }));
    const focused = document.activeElement;
    window.scrollTo(0, 0);
    await userEvent.keyboard("{Control>}z{/Control}");

    await expect.element(status).toHaveTextContent(fill(SENTENCES.back_to, { text: "was here" }));
    expect(status.element().firstElementChild).not.toBe(firstAnnouncement);
    await vi.waitFor(() => expect(window.scrollY).toBeGreaterThan(0));
    expect(document.activeElement).toBe(focused);
  });

  test("on a change's margin note, Cmd+Z sends focus to its span as the note goes, as pressing the note does", async () => {
    const screen = await openEditor();
    await changeSpan(screen, "is here");
    const note = screen.getByRole("button", { name: "Undo: “is here” goes back to “was here”" });
    await expect.element(note).toBeInTheDocument();
    note.element().focus();
    await userEvent.keyboard("{Control>}z{/Control}");

    await expect.element(note).not.toBeInTheDocument();
    await expect.element(screen.getByRole("button", { name: "was here" })).toHaveFocus();
  });

  test("on a change's margin note, Cmd+Shift+Z sends focus to its span as the note goes", async () => {
    const screen = await openEditor();
    await changeSpan(screen, "is here");
    // Typed back to the original, the span has no note; undone, it has again.
    await userEvent.keyboard("{Enter}");
    await screen.getByRole("textbox").fill("was here");
    await userEvent.keyboard("{Enter}");
    await expect.element(screen.getByRole("button", { name: /^Undo/ })).not.toBeInTheDocument();
    await userEvent.keyboard("{Control>}z{/Control}");
    const note = screen.getByRole("button", { name: "Undo: “is here” goes back to “was here”" });
    await expect.element(note).toBeInTheDocument();
    note.element().focus();
    await userEvent.keyboard("{Control>}{Shift>}z{/Shift}{/Control}");

    await expect.element(note).not.toBeInTheDocument();
    await expect.element(screen.getByRole("button", { name: "was here" })).toHaveFocus();
  });

  test("on a change's margin note that stays, Cmd+Z sends focus to its span, as one that goes does", async () => {
    const screen = await openEditor();
    await changeSpan(screen, "is here");
    await userEvent.keyboard("{Enter}");
    await screen.getByRole("textbox").fill("is there");
    await userEvent.keyboard("{Enter}");
    const note = screen.getByRole("button", { name: "Undo: “is there” goes back to “was here”" });
    await expect.element(note).toBeInTheDocument();
    note.element().focus();
    await userEvent.keyboard("{Control>}z{/Control}");

    await expect.element(screen.getByRole("button", { name: "Undo: “is here” goes back to “was here”" })).toBeVisible();
    await expect.element(screen.getByRole("button", { name: "is here" })).toHaveFocus();
  });

  test("on a phone, zoomed in, Cmd+Z on a change in the list under the page brings its span out from under the bar", async () => {
    const [wide, tall] = [window.innerWidth, window.innerHeight];
    await page.viewport(390, 844);
    try {
      // A similar font draws it, so the bar counts it, a row of its own here.
      const screen = await openEditor({ doc: { ...ARIAL_DOC, pages: [A4, A4] } });
      await expect.element(screen.getByRole("button", { name: "was here" })).toBeInTheDocument();
      for (let times = 0; times < 4; times++) await screen.getByRole("button", { name: "Zoom in" }).click();
      await expect.element(screen.getByRole("button", { name: "Fit to the window, now 150%" })).toBeVisible();
      await changeSpan(screen, "is here");
      const note = screen.getByRole("button", { name: "Undo: “is here” goes back to “was here”" });
      await expect.element(note).toBeVisible();
      note.element().focus();
      await userEvent.keyboard("{Control>}z{/Control}");

      const mark = screen.getByRole("button", { name: "was here" });
      await expect.element(mark).toHaveFocus();
      // What's held at the top over the pages: the bar, its rows wrapped, and the page list under it.
      const foot = screen.getByRole("banner").element().parentElement?.getBoundingClientRect().bottom ?? Infinity;
      await expect.poll(() => mark.element().getBoundingClientRect().top).toBeGreaterThanOrEqual(foot);
    } finally {
      await page.viewport(wide, tall);
    }
  });
});

async function attachThroughFonts(screen: Awaited<ReturnType<typeof render>>, file = FONT_FILE) {
  await screen.getByRole("button", { name: "Fonts" }).click();
  await expect.element(screen.getByRole("button", { name: "Use your copy of Arial" })).toBeVisible();
  // FileTrigger's input sits beside its button, unlabelled: the row's one file input.
  const chooser = screen.getByRole("dialog").element().querySelector("input[type=file]");
  if (!(chooser instanceof HTMLInputElement)) throw new Error("no file input in the fonts list");
  await userEvent.upload(chooser, file);
}

describe("the user's own copy of a font", () => {
  test("is offered while typing only once a letter typed is one the font can't draw", async () => {
    const screen = await openEditor({ doc: ARIAL_DOC, name: "letter.pdf" });

    await typeInSpan(screen, "was there");
    await expect.element(screen.getByRole("button", { name: "Use your copy" })).not.toBeInTheDocument();
    await screen.getByRole("textbox").fill("was Yes");
    await expect.element(screen.getByRole("button", { name: "Use your copy" })).toBeVisible();
  });

  test("shares the chip with why an edit won't match, side by side, neither over the other", async () => {
    // The file's own Times draws it, turned on the page, and has no Y: typed, a similar font draws the line.
    const turned = aSpan({ ...span, fidelity: "approximate", why: { code: "turned_text", params: {} } });
    const own = aDoc({ spans: [turned], fonts: [aFont("Times-Roman", { glyphs: LETTERS })] });
    const screen = await openEditor({ doc: own, name: "letter.pdf" });
    await typeInSpan(screen, "was Yes");

    const offer = screen.getByRole("button", { name: "Use your copy" });
    const similar = fill(SENTENCES.substitute, { font: "Liberation Serif Regular" });
    const why = screen.getByText(similar, { exact: false }).first();
    await expect.element(offer).toBeVisible();
    await expect.element(why).toBeVisible();
    const sentence = why.element().getBoundingClientRect();
    const pressed = offer.element().getBoundingClientRect();
    const apart = pressed.left >= sentence.right || pressed.top >= sentence.bottom;
    expect(apart).toBe(true);
    // On the page, not past its edge: the offer stays where a pointer can reach it.
    const sheet = screen.getByRole("region", { name: "Page 1" }).element().getBoundingClientRect();
    expect(pressed.right).toBeLessThanOrEqual(sheet.right);
  });

  test("a font file dropped on the field is the user's copy of the span's font", async () => {
    vi.mocked(putFont).mockResolvedValue(USERS_COPY);
    const screen = await openEditor({ doc: ARIAL_DOC, name: "letter.pdf" });
    await typeInSpan(screen, "was Yes");

    const field = screen.getByRole("textbox").element();
    // A file dragged from the desktop has an entry in the file system; one built here has none.
    const entry = vi.spyOn(DataTransferItem.prototype, "webkitGetAsEntry");
    entry.mockReturnValue({ isFile: true } as FileSystemEntry);
    const dropped = new DataTransfer();
    dropped.items.add(FONT_FILE);
    dropped.effectAllowed = "copy"; // as a file dragged from the desktop allows
    for (const type of ["dragenter", "dragover", "drop"]) {
      field.dispatchEvent(new DragEvent(type, { bubbles: true, cancelable: true, dataTransfer: dropped }));
    }

    await vi.waitFor(() => expect(putFont).toHaveBeenCalledWith(ARIAL_DOC.id, "Arial", FONT_FILE));
    entry.mockRestore();
  });

  test("text dragged onto the field is the field's to take, not a font", async () => {
    const screen = await openEditor({ doc: ARIAL_DOC, name: "letter.pdf" });
    await typeInSpan(screen, "was Yes");

    const field = screen.getByRole("textbox").element();
    const dragged = new DataTransfer();
    dragged.setData("text/plain", "there");
    dragged.effectAllowed = "copyMove"; // as text dragged from a page allows
    const events = ["dragenter", "dragover", "drop"].map(
      (type) => new DragEvent(type, { bubbles: true, cancelable: true, dataTransfer: dragged }),
    );
    for (const event of events) field.dispatchEvent(event);

    expect(events.map((event) => event.defaultPrevented)).toEqual([false, false, false]);
    expect(putFont).not.toHaveBeenCalled();
  });

  test("a file picker taking the window's focus leaves the typing as it was", async () => {
    const screen = await openEditor({ doc: ARIAL_DOC, name: "letter.pdf" });
    await typeInSpan(screen, "was Yes");
    const windowLeft = vi.spyOn(document, "hasFocus").mockReturnValue(false);

    screen
      .getByRole("textbox")
      .element()
      .dispatchEvent(new FocusEvent("focusout", { bubbles: true }));
    windowLeft.mockRestore();

    await expect.element(screen.getByRole("textbox")).toHaveValue("was Yes");
  });

  test("attached, its font draws the span and nothing pops up to say so", async () => {
    vi.mocked(putFont).mockResolvedValue(USERS_COPY);
    const screen = await openEditor({ doc: ARIAL_DOC, name: "letter.pdf" });

    await attachThroughFonts(screen);

    expect(putFont).toHaveBeenCalledWith(ARIAL_DOC.id, "Arial", FONT_FILE);
    await expect.element(screen.getByRole("button", { name: "Remove your copy of Arial" })).toBeVisible();
    // The button pressed is gone: focus goes to the one in its place, never to the page's body.
    await expect.element(screen.getByRole("button", { name: "Remove your copy of Arial" })).toHaveFocus();
    await expect.element(screen.getByRole("alert")).not.toBeInTheDocument();
  });

  test("attached, the field draws in it at once, until a letter it lacks is typed", async () => {
    vi.mocked(putFont).mockResolvedValue(USERS_COPY);
    // A real font file, so the browser can read it: one of ours, standing in for the user's Arial.
    const bytes = await (await fetch(SOME_FONT)).arrayBuffer();
    const screen = await openEditor({ doc: ARIAL_DOC, name: "letter.pdf" });
    await attachThroughFonts(screen, new File([bytes], "arial.ttf", { type: "font/ttf" }));
    await expect.element(screen.getByRole("button", { name: "Remove your copy of Arial" })).toHaveFocus();
    await userEvent.keyboard("{Escape}");

    await typeInSpan(screen, "was Yes");
    const field = screen.getByRole("textbox").element();
    expect(getComputedStyle(field).fontFamily).toContain("preview own Arial");
    // Your copy has no é: the server draws the line in the look-alike, so the preview does too.
    await screen.getByRole("textbox").fill("was Yesé");
    expect(getComputedStyle(field).fontFamily).toContain("preview Liberation Serif Regular");
  });

  test("refused, the server's sentence shows beside the font, warned", async () => {
    vi.mocked(putFont).mockRejectedValue(new ProblemError(aProblem(422, MISMATCH)));
    const screen = await openEditor({ doc: ARIAL_DOC, name: "letter.pdf" });

    await attachThroughFonts(screen);

    await expect.element(screen.getByRole("dialog").getByText(MISMATCH)).toBeVisible();
    await expect.element(screen.getByRole("button", { name: "Remove your copy of Arial" })).not.toBeInTheDocument();
  });
});

describe("a date's control in the field's slip", () => {
  beforeAll(() => learnDateLanguages());

  test("stays as the server judges the fonts again: a copy of the span's font dropped on the field", async () => {
    const words = "Due 14 March 2026";
    const glyphs = halfWideGlyphs(words);
    const due = { ...ARIAL_SPAN, text: words };
    const exact = { ...due, fidelity: "exact" as const, substitute: null };
    vi.mocked(putFont).mockResolvedValue(aDoc({ spans: [exact], fonts: [aFont("Arial", { attached: true, glyphs })] }));
    const screen = await openEditor({
      doc: aDoc({ spans: [due], fonts: [{ ...NAMED, glyphs }] }),
      name: "invoice.pdf",
    });
    const mark = screen.getByRole("button", { name: words });
    await expect.element(mark).toBeInTheDocument();
    mark.element().focus();
    await userEvent.keyboard("{Enter}");
    const control = screen.getByRole("button", { name: "Date" });
    await expect.element(control).toBeVisible();
    await expect.element(screen.getByText("Liberation Sans Regular")).toBeVisible();

    const field = screen.getByRole("textbox").element();
    // A file dragged from the desktop has an entry in the file system; one built here has none.
    const entry = vi.spyOn(DataTransferItem.prototype, "webkitGetAsEntry");
    entry.mockReturnValue({ isFile: true } as FileSystemEntry);
    const dropped = new DataTransfer();
    dropped.items.add(FONT_FILE);
    dropped.effectAllowed = "copy";
    for (const type of ["dragenter", "dragover", "drop"]) {
      field.dispatchEvent(new DragEvent(type, { bubbles: true, cancelable: true, dataTransfer: dropped }));
    }
    entry.mockRestore();

    // Judged again, the user's copy draws it: the field is still open, and its date still has its control.
    await expect.element(screen.getByText("Arial", { exact: true })).toBeVisible();
    await expect.element(screen.getByRole("textbox")).toHaveFocus();
    await expect.element(control).toBeVisible();
  });

  test.each([
    [
      "a date's calendar, on a day",
      "Due 14 March 2026",
      (screen: Awaited<ReturnType<typeof render>>) => screen.getByRole("button", { name: /March 14, 2026/ }),
    ],
    [
      "an amount's number field",
      "Pay 1.250,00 €",
      (screen: Awaited<ReturnType<typeof render>>) => screen.getByRole("textbox", { name: "Amount" }),
    ],
  ])("in %s, undo and redo take back nothing typed in the field, nor another edit", async (_, words, inEditorOf) => {
    const due = aSpan({ id: "due", text: words, bbox: { x0: 72, y0: 200, x1: 242, y1: 224 }, size: 20 });
    const glyphs = halfWideGlyphs(`abcdefghijklmnopqrstuvwxyz ${words}`);
    const screen = await openEditor({ doc: aDoc({ spans: [span, due], fonts: [aFont("Times-Roman", { glyphs })] }) });
    await changeSpan(screen, "is here");
    const mark = screen.getByRole("button", { name: words });
    await expect.element(mark).toBeInTheDocument();
    mark.element().focus();
    await userEvent.keyboard("{Enter}");
    // Typed before the editor opens, so the browser has the field's own typing to undo: emptied, then the line put
    // in at once, as a paste does, so one undo of the browser's would take the value away and close its editor.
    const field = screen.getByRole("textbox", { name: `Change “${words}”` });
    await userEvent.keyboard("{Backspace}");
    await expect.element(field).toHaveValue("");
    document.execCommand("insertText", false, `${words} ab`);
    await expect.element(field).toHaveValue(`${words} ab`);
    await userEvent.keyboard("{Alt>}{ArrowDown}{/Alt}");
    const inEditor = inEditorOf(screen);
    await expect.element(inEditor).toHaveFocus();

    for (const keys of [
      "{Control>}z{/Control}",
      "{Control>}{Shift>}z{/Shift}{/Control}",
      "{Control>}y{/Control}",
      "{Control>}z{/Control}",
    ]) {
      await userEvent.keyboard(keys);
      expect(field.element(), keys).toHaveValue(`${words} ab`);
      expect(document.activeElement, keys).toBe(inEditor.element());
      expect(screen.getByRole("button", { name: "is here" }).query(), keys).not.toBeNull();
    }
  });
});

/** The slip over or under the field, settled once it's placed and its arrival has run. */
async function settledSlip() {
  await new Promise(requestAnimationFrame);
  await new Promise(requestAnimationFrame);
  await Promise.all(document.getAnimations().map((animation) => animation.finished));
  return document.querySelector("[data-slip]")?.getBoundingClientRect() ?? new DOMRect();
}

/** Where what's held at the top over the pages ends: the bar, its rows wrapped, the page list, and the lines under it. */
function stickyFootOf(screen: Awaited<ReturnType<typeof render>>) {
  const top = screen.getByRole("banner").element().parentElement;
  if (top === null) return Infinity;
  return Math.max(...[top, ...top.children].map((box) => box.getBoundingClientRect().bottom));
}

/** Where the window ends under `slip`: at the zoom control's top where it lies under it, else at its foot. */
function windowFootUnder(slip: DOMRect, screen: Awaited<ReturnType<typeof render>>) {
  const foot = window.visualViewport?.height ?? window.innerHeight;
  const zoom = screen.getByRole("group", { name: "Zoom" }).element().getBoundingClientRect();
  return slip.left < zoom.right && slip.right > zoom.left ? Math.min(foot, zoom.top) : foot;
}

/** Runs `run` in a window `width` by `height`, then gives the window back its size. */
async function inWindow(width: number, height: number, run: () => Promise<void>) {
  const [wide, tall] = [window.innerWidth, window.innerHeight];
  await page.viewport(width, height);
  try {
    await run();
  } finally {
    await page.viewport(wide, tall);
  }
}

/** Scrolls the window so `element`'s foot is `y` down it. */
function scrollFootTo(element: Element, y: number) {
  window.scrollBy(0, element.getBoundingClientRect().bottom - y);
}

/** Opens the field on `mark` from the keyboard. */
async function openFieldOn(screen: Awaited<ReturnType<typeof render>>, mark: Element) {
  if (!(mark instanceof HTMLElement)) throw new Error("no mark");
  mark.focus();
  await userEvent.keyboard("{Enter}");
  await expect.element(screen.getByRole("textbox")).toHaveFocus();
}

describe("a slip near the window's foot", () => {
  beforeAll(() => learnDateLanguages());
  // What the window reports going wrong, as it does a slip's observer started again mid-delivery.
  const reported: string[] = [];
  const report = (event: ErrorEvent) => reported.push(event.message);
  beforeEach(() => {
    reported.length = 0;
    window.addEventListener("error", report);
  });
  afterEach(() => {
    window.removeEventListener("error", report);
    expect(reported).toEqual([]);
  });

  // A line low on the first of two pages, in a font with no é.
  const low = aSpan({ id: "low", text: "was low", size: 20, bbox: { x0: 72, y0: 640, x1: 152, y1: 664 } });
  const LOW = { ...aDoc({ spans: [low], fonts: [aFont("Times-Roman", { glyphs: LETTERS })] }), pages: [A4, A4] };
  // A date on the first of two pages, which opens a calendar.
  const DUE_WORDS = "Due 14 March 2026";
  const due = aSpan({ id: "due", text: DUE_WORDS, size: 12, bbox: { x0: 72, y0: 640, x1: 200, y1: 655 } });
  const DUE_FONT = aFont("Times-Roman", { glyphs: halfWideGlyphs(`${DUE_WORDS}0123456789`) });
  const DUE = { ...aDoc({ spans: [due], fonts: [DUE_FONT] }), pages: [A4, A4] };

  /** Opens the calendar of the date's field, open already. */
  async function openCalendar(screen: Awaited<ReturnType<typeof render>>) {
    const control = screen.getByRole("button", { name: "Date" });
    await expect.element(control).toBeVisible();
    await userEvent.keyboard("{Alt>}{ArrowDown}{/Alt}");
    await expect.element(control).toHaveAttribute("aria-expanded", "true");
  }

  test.each([
    [1440, 900],
    [390, 667],
  ])(
    "at %d px, hangs under its line while the window has room there, and the page doesn't move",
    async (width, height) => {
      await inWindow(width, height, async () => {
        const screen = await openEditor({ doc: LOW });
        const mark = screen.getByRole("button", { name: "was low" });
        await expect.element(mark).toBeInTheDocument();
        scrollFootTo(mark.element(), height / 3);
        const scrolled = window.scrollY;
        await openFieldOn(screen, mark.element());

        const slip = await settledSlip();
        expect(slip.top).toBeGreaterThanOrEqual(screen.getByRole("textbox").element().getBoundingClientRect().bottom);
        expect(slip.bottom).toBeLessThanOrEqual(windowFootUnder(slip, screen));
        expect(window.scrollY).toBe(scrolled);
      });
    },
  );

  test.each([
    [1440, 900],
    [390, 667],
  ])(
    "at %d px, opens over its line when the window has no room under it, whole in the window, and the page doesn't move",
    async (width, height) => {
      await inWindow(width, height, async () => {
        const screen = await openEditor({ doc: LOW });
        const mark = screen.getByRole("button", { name: "was low" });
        await expect.element(mark).toBeInTheDocument();
        // Clear of the zoom control, but under it the slip would pass the window's foot.
        scrollFootTo(mark.element(), height - 60);
        const scrolled = window.scrollY;
        await openFieldOn(screen, mark.element());

        const slip = await settledSlip();
        expect(slip.bottom).toBeLessThanOrEqual(screen.getByRole("textbox").element().getBoundingClientRect().top);
        expect(slip.top).toBeGreaterThanOrEqual(stickyFootOf(screen));
        expect(window.scrollY).toBe(scrolled);
      });
    },
  );

  test("on a phone, opens over a line near its page's top, over the page before, when the window has room there", async () => {
    await inWindow(390, 844, async () => {
      const top = aSpan({ id: "top", page: 2, text: "was top", size: 20, bbox: { x0: 72, y0: 40, x1: 152, y1: 64 } });
      const screen = await openEditor({
        doc: aDoc({ spans: [top], pages: [A4, A4, A4, A4], fonts: [aFont("Times-Roman")] }),
      });
      screen.getByRole("region", { name: "Page 3" }).element().scrollIntoView();
      const mark = screen.getByRole("button", { name: "was top" });
      await expect.element(mark).toBeInTheDocument();
      scrollFootTo(mark.element(), 780);
      const scrolled = window.scrollY;
      await openFieldOn(screen, mark.element());

      const slip = await settledSlip();
      expect(slip.bottom).toBeLessThanOrEqual(screen.getByRole("textbox").element().getBoundingClientRect().top);
      expect(slip.top).toBeGreaterThanOrEqual(stickyFootOf(screen));
      expect(window.scrollY).toBe(scrolled);
      // Drawn over the page before, not under it.
      const inside = document.elementFromPoint((slip.left + slip.right) / 2, slip.top + 20);
      expect(inside?.closest("[data-slip]")).not.toBeNull();
    });
  });

  /** Opens "was low" on a phone with room on neither side of it. */
  async function openWithRoomOnNeitherSide() {
    const screen = await openEditor({ doc: LOW });
    const mark = screen.getByRole("button", { name: "was low" });
    await expect.element(mark).toBeInTheDocument();
    // Halfway between the bar's foot and the zoom control's top: neither has room for the slip.
    scrollFootTo(mark.element(), 190);
    await openFieldOn(screen, mark.element());
    return screen;
  }

  test("on a phone, with room on neither side of its line, is brought into view under it, clear of the bar and the zoom control", async () => {
    await inWindow(390, 320, async () => {
      const screen = await openWithRoomOnNeitherSide();

      const slip = await settledSlip();
      const field = screen.getByRole("textbox").element().getBoundingClientRect();
      expect(slip.top).toBeGreaterThanOrEqual(field.bottom);
      expect(field.top).toBeGreaterThanOrEqual(stickyFootOf(screen));
      expect(slip.bottom).toBeLessThanOrEqual(windowFootUnder(slip, screen));
    });
  });

  test("on a phone, brought into view, is brought in again as typing grows it, as far as its top", async () => {
    await inWindow(390, 320, async () => {
      const screen = await openWithRoomOnNeitherSide();
      const opened = await settledSlip();
      // A letter the font lacks adds a line saying so.
      await userEvent.keyboard("é");
      await expect.poll(async () => (await settledSlip()).height).toBeGreaterThan(opened.height);

      // Taller now than the window has room for: brought in as far as its top, which holds the verdict.
      const slip = await settledSlip();
      expect(slip.height).toBeGreaterThan(windowFootUnder(slip, screen) - stickyFootOf(screen));
      expect(slip.top - stickyFootOf(screen)).toBeGreaterThanOrEqual(0);
      expect(slip.top - stickyFootOf(screen)).toBeLessThan(1);
    });
  });

  test("on a phone, keeps to its side of its line as typing grows it, brought into view there", async () => {
    await inWindow(390, 667, async () => {
      const screen = await openEditor({ doc: LOW });
      const mark = screen.getByRole("button", { name: "was low" });
      await expect.element(mark).toBeInTheDocument();
      scrollFootTo(mark.element(), 300);
      await openFieldOn(screen, mark.element());
      const opened = await settledSlip();
      // Its foot just over the zoom control: a line more and it passes it, with room over its line.
      window.scrollBy(0, opened.bottom - windowFootUnder(opened, screen) + 4);
      await userEvent.keyboard("é");
      await expect.poll(async () => (await settledSlip()).height).toBeGreaterThan(opened.height);

      const slip = await settledSlip();
      expect(slip.top).toBeGreaterThanOrEqual(screen.getByRole("textbox").element().getBoundingClientRect().bottom);
      expect(slip.bottom).toBeLessThanOrEqual(windowFootUnder(slip, screen));
    });
  });

  test("on a phone, taller than the window has room for under its line, keeps its top in view", async () => {
    await inWindow(390, 520, async () => {
      const screen = await openEditor({ doc: DUE });
      const mark = screen.getByRole("button", { name: DUE_WORDS });
      await expect.element(mark).toBeInTheDocument();
      // Just under the bar.
      scrollFootTo(mark.element(), stickyFootOf(screen) + 20);
      await openFieldOn(screen, mark.element());
      await openCalendar(screen);

      const slip = await settledSlip();
      expect(slip.top).toBeGreaterThanOrEqual(screen.getByRole("textbox").element().getBoundingClientRect().bottom);
      expect(slip.top).toBeGreaterThanOrEqual(stickyFootOf(screen));
    });
  });

  test("on a phone, over its line, grows up from it as its calendar opens, its top brought into view under the bar", async () => {
    await inWindow(390, 560, async () => {
      const screen = await openEditor({ doc: DUE });
      const mark = screen.getByRole("button", { name: DUE_WORDS });
      await expect.element(mark).toBeInTheDocument();
      scrollFootTo(mark.element(), 480);
      await openFieldOn(screen, mark.element());
      await openCalendar(screen);

      const slip = await settledSlip();
      expect(slip.bottom).toBeLessThanOrEqual(screen.getByRole("textbox").element().getBoundingClientRect().top);
      expect(slip.top).toBeGreaterThanOrEqual(stickyFootOf(screen));
    });
  });

  test("on a phone, clear of the lines held under the bar", async () => {
    const said = "This PDF is signed. Any change breaks the signature, and so does downloading it from here.";
    const signed = { ...LOW, signed: true, notices: [aDocumentNotice(said, "signed_file")] };
    await inWindow(390, 340, async () => {
      const screen = await openEditor({ doc: signed });
      await expect.element(screen.getByText(said, { exact: true }).first()).toBeVisible();
      const mark = screen.getByRole("button", { name: "was low" });
      await expect.element(mark).toBeInTheDocument();
      scrollFootTo(mark.element(), 270);
      await openFieldOn(screen, mark.element());

      const slip = await settledSlip();
      expect(slip.top).toBeGreaterThanOrEqual(stickyFootOf(screen));
      expect(slip.bottom).toBeLessThanOrEqual(windowFootUnder(slip, screen));
    });
  });

  test.each([
    [1440, 900],
    [390, 844],
    [390, 667],
  ])(
    "at %d by %d px, near the last page's foot, opens its calendar whole in the window, the pages scrolling no way but sideways",
    async (width, height) => {
      await inWindow(width, height, async () => {
        const words = "Due 14 March 2026";
        const glyphs = halfWideGlyphs(`${words}0123456789`);
        const last = aSpan({ id: "due", text: words, size: 12, bbox: { x0: 72, y0: 800, x1: 200, y1: 815 } });
        const screen = await openEditor({ doc: aDoc({ spans: [last], fonts: [aFont("Times-Roman", { glyphs })] }) });
        const mark = screen.getByRole("button", { name: words });
        await expect.element(mark).toBeInTheDocument();
        window.scrollTo(0, document.documentElement.scrollHeight);
        await openFieldOn(screen, mark.element());
        await openCalendar(screen);

        const slip = await settledSlip();
        const pages = document.getElementById("pages");
        if (pages === null) throw new Error("no pages");
        expect(pages.scrollHeight).toBeLessThanOrEqual(pages.clientHeight);
        expect(slip.bottom).toBeLessThanOrEqual(windowFootUnder(slip, screen));
        expect(slip.top).toBeGreaterThanOrEqual(stickyFootOf(screen));
      });
    },
  );
});

describe("splitting the document", () => {
  test("Split here sits between pages only, and splits the document after the page above it", async () => {
    vi.mocked(exportPdf)
      .mockReset()
      .mockReturnValue(new Promise(() => undefined));
    const opened = { ...DOC, pages: [A4, A4, A4] };
    const screen = await openEditor({ doc: opened });
    await expect.element(screen.getByRole("button", { name: "Split after page 2" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Split after/ }).elements()).toHaveLength(2);
    await screen.getByRole("button", { name: "Split after page 1" }).click();
    expect(exportPdf).toHaveBeenCalledWith("doc", [], { pages: [0] });
    // The second file waits for the first; meanwhile neither split asks again.
    await expect.element(screen.getByRole("button", { name: "Split after page 2" })).toBeDisabled();
  });
});

describe("deleting a page", () => {
  // Four pages; the third has two lines, and "here" is on the first and the third.
  const at = (y: number) => ({ x0: 72, y0: y, x1: 200, y1: y + 24 });
  const FOUR = aDoc({
    spans: [
      aSpan({ id: "one", text: "here on one", size: 20, bbox: at(100) }),
      aSpan({ id: "three", text: "here on three", size: 20, page: 2, bbox: at(100) }),
      aSpan({ id: "also", text: "also on three", size: 20, page: 2, bbox: at(140) }),
    ],
    // Stored in the file, with every letter these tests type.
    fonts: [aFont("Times-Roman", { glyphs: LETTERS })],
    pages: [A4, A4, A4, A4],
  });
  const deleteButton = (screen: Awaited<ReturnType<typeof render>>, shownNumber: number) =>
    screen.getByRole("button", { name: `Delete page ${shownNumber}`, exact: true });
  // The slip in the third page's place: in the document, as the live region says it too.
  const slip = (screen: Awaited<ReturnType<typeof render>>) =>
    screen.getByRole("main").getByText("This page is left out of the download");

  /** Change the line `name` on page `region`'s sheet to `text`, the page brought near so its marks are drawn. */
  async function changeLine(screen: Awaited<ReturnType<typeof render>>, { region = "Page 3", name = "", text = "" }) {
    screen.getByRole("region", { name: region, exact: true }).element().scrollIntoView();
    const mark = screen.getByRole("button", { name, exact: true });
    await expect.element(mark).toBeInTheDocument();
    mark.element().focus();
    await userEvent.keyboard("{Enter}");
    await screen.getByRole("textbox", { name: `Change “${name}”` }).fill(text);
    await userEvent.keyboard("{Enter}");
  }

  test("leaves a slip in the page's place with Put back focused, and the bar counts the download's pages", async () => {
    const screen = await openEditor({ doc: FOUR });
    await deleteButton(screen, 3).click();
    await expect.element(slip(screen)).toBeVisible();
    await expect.element(screen.getByRole("button", { name: "Put back" })).toHaveFocus();
    await expect.element(screen.getByRole("status")).toHaveTextContent("This page is left out of the download");
    // Read from the top: focus on Put back brought the slip into view.
    window.scrollTo(0, 0);
    await expect.element(screen.getByRole("banner").getByText("Page 1 of 3")).toBeVisible();
    // A delete changes no words: the bar keeps its forecast, here of nothing, until one changes.
    await expect.element(screen.getByRole("banner").getByText("1 change")).not.toBeInTheDocument();
    // Labels count the pages as shown: the page after the slip is the third now.
    expect(screen.getByRole("region", { name: /^Page \d$/ }).elements()).toHaveLength(3);
    await expect.element(deleteButton(screen, 3)).toBeInTheDocument();
    await expect.element(deleteButton(screen, 4)).not.toBeInTheDocument();
  });

  test("its button is named on hover, and Tab reaches it after the page above and before its own first line", async () => {
    const screen = await openEditor({ doc: FOUR });
    await deleteButton(screen, 1).hover();
    await expect.element(screen.getByRole("tooltip")).toHaveTextContent("Delete page 1");
    deleteButton(screen, 1).element().focus();
    await userEvent.keyboard("{Tab}");
    await expect.element(screen.getByRole("button", { name: "here on one" })).toHaveFocus();
    deleteButton(screen, 2).element().focus();
    await userEvent.keyboard("{Shift>}{Tab}{/Shift}");
    await expect.element(screen.getByRole("button", { name: "Split after page 1" })).toHaveFocus();
  });

  test("its edits leave the changes' count, and Cmd+Z puts it back with them, focus on its button", async () => {
    const screen = await openEditor({ doc: FOUR });
    await changeLine(screen, { region: "Page 1", name: "here on one", text: "now on one" });
    await changeLine(screen, { name: "here on three", text: "now on three" });
    await changeLine(screen, { name: "also on three", text: "now also on three" });
    await expect.element(screen.getByRole("banner").getByText("3 changes")).toBeVisible();
    await deleteButton(screen, 3).click();
    // The first page's edit and the delete.
    await expect.element(screen.getByRole("banner").getByText("2 changes")).toBeVisible();

    await userEvent.keyboard("{Control>}z{/Control}");
    await expect.element(slip(screen)).not.toBeInTheDocument();
    await expect.element(screen.getByRole("banner").getByText("3 changes")).toBeVisible();
    await expect.element(screen.getByRole("button", { name: "now on three", exact: true })).toBeInTheDocument();
    await expect.element(deleteButton(screen, 3)).toHaveFocus();
    await expect.element(screen.getByRole("status")).toHaveTextContent("Page 3 is back in the download");
  });

  test("undone from anywhere but its own controls, the delete leaves focus where it is", async () => {
    const screen = await openEditor({ doc: FOUR });
    await deleteButton(screen, 3).click();
    screen.getByRole("region", { name: "Page 1", exact: true }).element().scrollIntoView();
    const line = screen.getByRole("button", { name: "here on one", exact: true });
    await expect.element(line).toBeInTheDocument();
    line.element().focus();
    await userEvent.keyboard("{Control>}z{/Control}");
    await expect.element(screen.getByRole("status")).toHaveTextContent("Page 3 is back in the download");
    await expect.element(line).toHaveFocus();
  });

  test("a render's notice about it waits while it's deleted, and comes back with Put back", async () => {
    const drewOtherwise = "This page wouldn't take Times, so the line is drawn in Liberation Serif.";
    vi.mocked(renderStrips).mockResolvedValue(aReply({ notices: [aSpanNotice(drewOtherwise, "three")] }));
    const screen = await openEditor({ doc: FOUR });
    await changeLine(screen, { name: "here on three", text: "now on three" });
    const notice = screen.getByRole("paragraph").filter({ hasText: drewOtherwise });
    await expect.element(notice).toBeVisible();
    await deleteButton(screen, 3).click();
    await expect.element(notice).not.toBeInTheDocument();
    await screen.getByRole("button", { name: "Put back" }).click();
    await expect.element(notice).toBeVisible();
  });

  test("redone from anywhere on its page, the delete puts focus on Put back", async () => {
    const screen = await openEditor({ doc: FOUR });
    await deleteButton(screen, 3).click();
    await userEvent.keyboard("{Control>}z{/Control}");
    await expect.element(deleteButton(screen, 3)).toHaveFocus();
    screen.getByRole("region", { name: "Page 3", exact: true }).element().scrollIntoView();
    const line = screen.getByRole("button", { name: "here on three", exact: true });
    await expect.element(line).toBeInTheDocument();
    line.element().focus();
    await userEvent.keyboard("{Control>}{Shift>}z{/Shift}{/Control}");
    await expect.element(screen.getByRole("button", { name: "Put back" })).toHaveFocus();
  });

  // Persisted before the server had the sentences a deleted page takes.
  const persistedEarlier = () => {
    const older = { ...SENTENCES };
    Reflect.deleteProperty(older, "page_left_out");
    Reflect.deleteProperty(older, "page_put_back");
    const persisted = { work: { history: EMPTY_HISTORY, fonts: [] }, until: Date.now() + 60_000 };
    return openEditor({ doc: { ...FOUR, sentences: older }, persisted });
  };

  test("with a reply persisted by an earlier build not read again, delete, Put back and undo work, and why stays in the bar until the connection comes back", async () => {
    const busy = "We can't work on files right now. Try again in a few minutes.";
    vi.mocked(documentOf)
      .mockReset()
      .mockRejectedValue(new ProblemError(aProblem(503, busy)));
    const screen = await persistedEarlier();
    const said = screen.getByRole("banner").getByText(busy);
    await expect.element(said).toBeVisible();

    await deleteButton(screen, 3).click();
    await expect.element(screen.getByRole("button", { name: "Put back" })).toHaveFocus();
    await screen.getByRole("button", { name: "Put back" }).click();
    await expect.element(deleteButton(screen, 3)).toHaveFocus();
    await deleteButton(screen, 3).click();
    await userEvent.keyboard("{Control>}z{/Control}");
    await expect.element(deleteButton(screen, 3)).toHaveFocus();
    await expect.element(said).toBeVisible();

    vi.mocked(documentOf).mockResolvedValue(FOUR);
    window.dispatchEvent(new Event("online"));
    await expect.element(said).not.toBeInTheDocument();
    await deleteButton(screen, 3).click();
    await expect.element(slip(screen)).toBeVisible();
  });

  test("opened again from a reply persisted by an earlier build, its sentences are asked for again", async () => {
    vi.mocked(documentOf).mockReset().mockResolvedValue(FOUR);
    const screen = await persistedEarlier();
    await vi.waitFor(() => expect(documentOf).toHaveBeenCalledWith("doc"));
    await deleteButton(screen, 3).click();
    await expect.element(slip(screen)).toBeVisible();
    await screen.getByRole("button", { name: "Put back" }).click();
    await expect.element(screen.getByRole("status")).toHaveTextContent("Page 3 is back in the download");
  });

  test("Put back brings the page back with its edits, focus on its button", async () => {
    const screen = await openEditor({ doc: FOUR });
    await changeLine(screen, { name: "here on three", text: "now on three" });
    await deleteButton(screen, 3).click();
    await screen.getByRole("button", { name: "Put back" }).click();
    await expect.element(slip(screen)).not.toBeInTheDocument();
    await expect.element(screen.getByRole("button", { name: "now on three", exact: true })).toBeInTheDocument();
    await expect.element(screen.getByRole("banner").getByText("1 change")).toBeVisible();
    await expect.element(deleteButton(screen, 3)).toHaveFocus();
  });

  test("find leaves it out of the count, and Change all leaves its words as they were", async () => {
    const screen = await openEditor({ doc: FOUR });
    await deleteButton(screen, 3).click();
    await userEvent.keyboard("{Control>}f{/Control}");
    await userEvent.keyboard("here");
    const bar = screen.getByRole("search");
    await expect.element(bar.getByText("1 place will look identical")).toBeVisible();
    await screen.getByRole("textbox", { name: "Change to" }).fill("there");
    await screen.getByRole("button", { name: "Change all" }).click();
    await expect.element(bar.getByText(/^Changed 1 place/)).toBeVisible();
    screen.getByRole("region", { name: "Page 1", exact: true }).element().scrollIntoView();
    await expect.element(screen.getByRole("button", { name: "there on one", exact: true })).toBeInTheDocument();

    // Put back, the page's line reads as it did.
    await screen.getByRole("button", { name: "Put back" }).click();
    await changeLine(screen, { name: "here on three", text: "here on three, put back" });
  });

  test("the last page left can't be deleted", async () => {
    const screen = await openEditor({ doc: { ...FOUR, pages: [A4, A4] } });
    await expect.element(deleteButton(screen, 2)).toBeEnabled();
    await deleteButton(screen, 1).click();
    await expect.element(deleteButton(screen, 1)).toBeDisabled();
  });

  test("Split here keeps its place beside the slip, and splits the pages as shown", async () => {
    vi.mocked(exportPdf)
      .mockReset()
      .mockReturnValue(new Promise(() => undefined));
    const screen = await openEditor({ doc: FOUR });
    await deleteButton(screen, 3).click();
    const splits = screen.getByRole("button", { name: /^Split after/ });
    await expect
      .poll(() => splits.elements().map((split) => split.getAttribute("aria-label")))
      .toEqual(["Split after page 1", "Split after page 2"]);
    await screen.getByRole("button", { name: "Split after page 2" }).click();
    expect(exportPdf).toHaveBeenCalledWith("doc", [{ kind: "delete_page", page: 2 }], { pages: [0, 1] });
  });

  test("redone from its tick in the page list, the delete puts focus on Put back", async () => {
    const screen = await openEditor({ doc: FOUR });
    await deleteButton(screen, 3).click();
    await userEvent.keyboard("{Control>}z{/Control}");
    await expect.element(deleteButton(screen, 3)).toHaveFocus();
    const tick = screen.getByRole("navigation", { name: "Pages" }).getByRole("link", { name: "Page 3", exact: true });
    tick.element().focus();
    await userEvent.keyboard("{Control>}{Shift>}z{/Shift}{/Control}");
    await expect.element(screen.getByRole("button", { name: "Put back" })).toHaveFocus();
  });

  test("the page list goes to a page, and counts the download's pages", async () => {
    const screen = await openEditor({ doc: FOUR });
    const list = screen.getByRole("navigation", { name: "Pages" });
    await expect.element(list.getByText("1/4")).toBeVisible();
    await expect
      .element(list.getByRole("link", { name: "Page 1", exact: true }))
      .toHaveAttribute("aria-current", "page");
    await list.getByRole("link", { name: "Page 3", exact: true }).click();
    await expect.element(deleteButton(screen, 3)).toHaveFocus();
    await deleteButton(screen, 2).click();
    await expect.poll(() => list.getByRole("link").elements().length).toBe(3);
  });
});

describe("a file dropped on the editor", () => {
  /** Drop `files` anywhere on the editor, as from the desktop; whether the browser was kept from it. */
  function dropOnPage(files: File[]): boolean {
    const data = new DataTransfer();
    for (const file of files) data.items.add(file);
    const drop = new DragEvent("drop", { dataTransfer: data, bubbles: true, cancelable: true });
    document.body.dispatchEvent(drop);
    return drop.defaultPrevented;
  }
  const PDF = new File(["%PDF-"], "invoice.pdf", { type: "application/pdf" });

  test("never reaches the browser; a PDF opens, but not over changes no export has taken, and a font says where it goes", async () => {
    const onOpen = vi.fn();
    const screen = await openEditor({ onOpen });
    await expect.element(screen.getByRole("button", { name: "was here" })).toBeInTheDocument();
    expect(dropOnPage([PDF])).toBe(true);
    expect(onOpen).toHaveBeenCalledWith(PDF);

    await changeSpan(screen, "is here");
    expect(dropOnPage([PDF])).toBe(true);
    expect(onOpen).toHaveBeenCalledTimes(1);
    const lost = "Export your changes first, or undo them: opening another PDF here would lose them.";
    // Under the bar, and heard.
    await expect.element(screen.getByText(lost).first()).toBeVisible();
    await expect.element(screen.getByRole("status")).toHaveTextContent(lost);

    expect(dropOnPage([new File(["font"], "arial.ttf", { type: "font/ttf" })])).toBe(true);
    await expect.element(screen.getByText(/^Only a PDF can be dropped here/).first()).toBeVisible();
    expect(onOpen).toHaveBeenCalledTimes(1);
  });
});

describe("find and replace", () => {
  test("Cmd+F opens find with focus in it; it counts, changes all, and Escape gives focus back to Find", async () => {
    const screen = await openEditor();
    await expect.element(screen.getByRole("button", { name: "was here" })).toBeInTheDocument();
    await userEvent.keyboard("{Control>}f{/Control}");
    const find = screen.getByRole("textbox", { name: "Find" });
    await expect.element(find).toHaveFocus();

    await userEvent.keyboard("here");
    await expect.element(screen.getByRole("search").getByText("1 place will look identical")).toBeVisible();
    await screen.getByRole("textbox", { name: "Change to" }).fill("there");
    await screen.getByRole("button", { name: "Change all" }).click();
    await expect.element(screen.getByRole("button", { name: "was there" })).toBeInTheDocument();
    await expect.element(screen.getByRole("search").getByText(/^Changed 1 place/)).toBeVisible();

    await screen.getByRole("textbox", { name: "Find" }).click();
    await userEvent.keyboard("{Escape}");
    await expect.element(screen.getByRole("search")).not.toBeInTheDocument();
    await expect.element(screen.getByRole("button", { name: /^Find/ })).toHaveFocus();
  });

  test("the step's place among those found agrees with the count beside it while a span is typed in", async () => {
    const there = aSpan({ id: "s2", text: "is there", size: 20, bbox: { x0: 72, y0: 200, x1: 152, y1: 224 } });
    const screen = await openEditor({ doc: aDoc({ spans: [span, there], fonts: [aFont("Times-Roman")] }) });
    await expect.element(screen.getByRole("button", { name: "was here" })).toBeInTheDocument();
    await userEvent.keyboard("{Control>}f{/Control}");
    await userEvent.keyboard("was");
    const bar = screen.getByRole("search");
    await expect.element(bar.getByText("1 place will look identical")).toBeVisible();
    await userEvent.keyboard("{Enter}");
    await expect.element(bar.getByText("1 of 1")).toBeVisible();
    await screen.getByRole("button", { name: "is there" }).click();
    await userEvent.keyboard("{Control>}a{/Control}was there");
    await expect.element(bar.getByText(/^2 places, 1 of them/)).toBeVisible();
    await expect.element(bar.getByText("1 of 2")).toBeVisible();
  });

  test("Change all is off while every place found would be left empty, and the count says why", async () => {
    const screen = await openEditor();
    await expect.element(screen.getByRole("button", { name: "was here" })).toBeInTheDocument();
    await userEvent.keyboard("{Control>}f{/Control}");
    await userEvent.keyboard("was here");
    const bar = screen.getByRole("search");
    await bar.getByRole("textbox", { name: "Change to" }).fill(" ");
    await expect.element(bar.getByText("1 place can't be left empty")).toBeVisible();
    await expect.element(bar.getByRole("button", { name: "Change all" })).toBeDisabled();
  });

  test("Change all puts focus in Change to, whether it stays on or turns off under it", async () => {
    const screen = await openEditor();
    await expect.element(screen.getByRole("button", { name: "was here" })).toBeInTheDocument();
    await userEvent.keyboard("{Control>}f{/Control}");
    await userEvent.keyboard("here");
    const bar = screen.getByRole("search");
    const changeTo = bar.getByRole("textbox", { name: "Change to" });
    const changeAll = bar.getByRole("button", { name: "Change all" });
    // Changed, and "here" still found: it stays on.
    await changeTo.fill("here and there");
    await expect.element(changeAll).toBeEnabled();
    changeAll.element().focus();
    await userEvent.keyboard("{Enter}");
    await expect.element(screen.getByRole("button", { name: "was here and there" })).toBeInTheDocument();
    await expect.element(changeAll).toBeEnabled();
    await expect.element(changeTo).toHaveFocus();
    // Changed, and nothing reads "here" now: it turns off.
    await changeTo.fill("this");
    await expect.element(bar.getByText(/^1 place/)).toBeVisible();
    changeAll.element().focus();
    await userEvent.keyboard("{Enter}");
    await expect.element(bar.getByText(/^Changed 1 place/)).toBeVisible();
    await expect.element(changeAll).toBeDisabled();
    await expect.element(changeTo).toHaveFocus();
  });

  test("the bar opened again after a Change all puts focus in Find, its words selected, as every open does", async () => {
    const screen = await openEditor();
    await expect.element(screen.getByRole("button", { name: "was here" })).toBeInTheDocument();
    await userEvent.keyboard("{Control>}f{/Control}");
    await userEvent.keyboard("here");
    const bar = screen.getByRole("search");
    await bar.getByRole("textbox", { name: "Change to" }).fill("there");
    await bar.getByRole("button", { name: "Change all" }).click();
    await expect.element(bar.getByText(/^Changed 1 place/)).toBeVisible();
    await userEvent.keyboard("{Escape}");
    await expect.element(bar).not.toBeInTheDocument();
    await userEvent.keyboard("{Control>}f{/Control}");
    const find = screen.getByRole("textbox", { name: "Find" });
    await expect.element(find).toHaveFocus();
    const input = find.element() as HTMLInputElement;
    expect([input.selectionStart, input.selectionEnd]).toEqual([0, "here".length]);
  });

  test("a reading persisted before Change all's sentence was sent changes all, with no key hint left alone", async () => {
    // The server's reading, asked for again, hasn't landed.
    vi.mocked(documentOf)
      .mockReset()
      .mockReturnValue(new Promise<Document>(() => undefined));
    const stale = structuredClone(DOC);
    Reflect.deleteProperty(stale.sentences, "changed_places");
    const screen = await openEditor({ doc: stale });
    await expect.element(screen.getByRole("button", { name: "was here" })).toBeInTheDocument();
    await userEvent.keyboard("{Control>}f{/Control}");
    await userEvent.keyboard("here");
    const bar = screen.getByRole("search");
    await expect.element(bar.getByText("1 place will look identical")).toBeVisible();
    await bar.getByRole("textbox", { name: "Change to" }).fill("there");
    await bar.getByRole("button", { name: "Change all" }).click();
    await expect.element(screen.getByRole("button", { name: "was there" })).toBeInTheDocument();
    await expect.element(bar.getByText("puts it back", { exact: false })).not.toBeInTheDocument();
  });

  test("the find bar keeps its height as Find is typed into and its count settles, at every width", async () => {
    const screen = await openEditor();
    await expect.element(screen.getByRole("button", { name: "was here" })).toBeInTheDocument();
    const [wide, tall] = [window.innerWidth, window.innerHeight];
    // A phone, the narrow layout's widest, a bar too narrow for the count beside the fields, and a desk.
    for (const [width, height] of [
      [390, 844],
      [820, 900],
      [1000, 800],
      [1440, 900],
    ] as const) {
      await page.viewport(width, height);
      await userEvent.keyboard("{Control>}f{/Control}");
      const bar = screen.getByRole("search");
      const heights = new Set<number>();
      const watch = new ResizeObserver(() => heights.add(bar.element().getBoundingClientRect().height));
      watch.observe(bar.element());
      // Found, then typed on (nothing counted while the delay runs), then nothing found, then found again.
      await userEvent.keyboard("her");
      await expect.element(bar.getByText("1 place will look identical")).toBeVisible();
      await userEvent.keyboard("x");
      await expect.element(bar.getByText("Nothing matches")).toBeVisible();
      await userEvent.keyboard("{Backspace}");
      await expect.element(bar.getByText("1 place will look identical")).toBeVisible();
      watch.disconnect();
      expect([width, [...heights]]).toEqual([width, [bar.element().getBoundingClientRect().height]]);
      await userEvent.keyboard("{Escape}");
    }
    await page.viewport(wide, tall);
  });

  test("Match case and Whole words are checkboxes beside the fields, and find again as they're ticked", async () => {
    const screen = await openEditor();
    await expect.element(screen.getByRole("button", { name: "was here" })).toBeInTheDocument();
    await userEvent.keyboard("{Control>}f{/Control}");
    await userEvent.keyboard("HER");
    const bar = screen.getByRole("search");
    await expect.element(bar.getByText("1 place will look identical")).toBeVisible();
    // A label, as a pointer presses it: the box itself is hidden from the pointer, never from a screen reader.
    await bar.getByText("Match case").click();
    await expect.element(bar.getByRole("checkbox", { name: "Match case" })).toBeChecked();
    await expect.element(bar.getByText("Nothing matches")).toBeVisible();
    await bar.getByText("Match case").click();
    await bar.getByText("Whole words").click();
    await expect.element(bar.getByText("Nothing matches")).toBeVisible();
    await userEvent.keyboard("{Escape}");
    await expect.element(bar).not.toBeInTheDocument();
  });
});

/** Double-click the first page's bare paper at `at`, in its own points; where that is on screen. */
async function doubleClickPaper(screen: Awaited<ReturnType<typeof render>>, [x, y]: [number, number]) {
  const pageOne = screen.getByRole("region", { name: "Page 1" }).element();
  await expect.poll(() => pageOne.querySelector("img")).not.toBeNull();
  const layer = pageOne.querySelector("img")?.parentElement;
  if (layer == null) throw new Error("the page's layer holds its image");
  const shown = layer.getBoundingClientRect();
  const position = { x: (x / A4.width) * shown.width, y: (y / A4.height) * shown.height };
  await userEvent.dblClick(page.elementLocator(layer), { position });
  return { x: shown.left + position.x, y: shown.top + position.y };
}

/** The slip of new text being typed, on the page's layer for slips: the one slip open. */
function newTextSlip() {
  const slip = document.querySelector("[data-slip]");
  if (slip === null) throw new Error("no slip");
  return page.elementLocator(slip);
}

/** The edits the last render asked for. */
function lastRendered() {
  return vi.mocked(renderStrips).mock.lastCall?.[1].edits ?? [];
}

describe("new text", () => {
  // Red text: new text near it takes its colour. Its font's own copy draws every letter typed here.
  const red = aSpan({ ...span, color: [0.8, 0, 0] });
  const RED_DOC = aDoc({
    spans: [red],
    fonts: [aFont("Times-Roman", { glyphs: halfWideGlyphs("SignedMH. alvorsn") })],
  });

  test("a double-click on bare paper opens a field at the press, in the nearest span's font, size and colour", async () => {
    const screen = await openEditor({ doc: RED_DOC });
    const pressed = await doubleClickPaper(screen, [300, 300]);

    const field = screen.getByRole("textbox", { name: "New text" });
    await expect.element(field).toHaveFocus();
    const shown = field.element().getBoundingClientRect();
    expect(Math.abs(shown.left - pressed.x)).toBeLessThan(1);
    // The line's middle sits at the press.
    expect(Math.abs((shown.top + shown.bottom) / 2 - pressed.y)).toBeLessThan(3);
    expect(getComputedStyle(field.element()).color).toBe("rgb(204, 0, 0)");
    // The chip says what draws it, and at what size: the span's own font, which draws it as it is.
    const size = screen.getByText("20 pt");
    await expect.element(size).toBeVisible();
    expect(size.element().parentElement?.textContent).toBe("Times Roman20 ptlooks identical");
    // The faces: the page's own first, then the handwriting ones we ship, each named in its own face.
    const faces = screen.getByRole("listbox", { name: "Font" });
    await expect.element(faces.getByRole("option", { name: "Times Roman", selected: true })).toBeVisible();
    await expect.element(faces.getByRole("option", { name: "Caveat Bold" })).toBeVisible();
  });

  test("Enter adds one insert to the render's edits, in the nearest span's style", async () => {
    const screen = await openEditor({ doc: RED_DOC });
    await doubleClickPaper(screen, [300, 300]);
    await userEvent.keyboard("Signed");
    await userEvent.keyboard("{Enter}");

    await expect.poll(lastRendered).toHaveLength(1);
    const [inserted] = lastRendered();
    expect(inserted).toMatchObject({ kind: "insert", page: 0, text: "Signed", size: 20, font: "Times-Roman" });
    expect(inserted?.kind === "insert" && inserted.color).toEqual([0.8, 0, 0]);
    await expect.element(screen.getByRole("banner").getByText("1 change")).toBeVisible();
  });

  test("Escape with nothing typed adds none", async () => {
    const screen = await openEditor({ doc: RED_DOC });
    await doubleClickPaper(screen, [300, 300]);
    await expect.element(screen.getByRole("textbox", { name: "New text" })).toHaveFocus();
    await userEvent.keyboard("{Escape}");

    await expect.element(screen.getByRole("textbox", { name: "New text" })).not.toBeInTheDocument();
    expect(renderStrips).not.toHaveBeenCalled();
  });

  test("on a touch screen its field is set at 16 px, so iOS doesn't zoom, and drawn as a mouse's is", async () => {
    const screen = await openEditor({ doc: RED_DOC });
    await doubleClickPaper(screen, [300, 300]);
    const field = screen.getByRole("textbox", { name: "New text" });
    await expect.element(field).toHaveFocus();
    // Where it sits on its page: a touch screen's bar is taller, so the page is lower.
    const onPage = () => {
      const box = field.element().getBoundingClientRect();
      const sheet = field.element().closest("section")?.querySelector("img")?.getBoundingClientRect();
      return { top: box.top - (sheet?.top ?? 0), height: box.height };
    };
    const atDesk = { ...onPage(), fontPx: getComputedStyle(field.element()).fontSize };
    await userEvent.keyboard("{Escape}");
    await expect.element(field).not.toBeInTheDocument();

    await emulateTouchScreen();
    await doubleClickPaper(screen, [300, 300]);
    await expect.element(field).toHaveFocus();
    expect(getComputedStyle(field.element()).fontSize).toBe("16px");
    // Its letters drawn at the text's size, once the page's is known: 16 px, scaled down.
    const scale = () => new DOMMatrix(getComputedStyle(field.element()).transform).a;
    await expect.poll(scale).toBeLessThan(1);
    expect(16 * scale()).toBeCloseTo(parseFloat(atDesk.fontPx), 1);
    // As tall as its line, so a press on the line's middle lands on it.
    expect(onPage().height).toBeCloseTo(atDesk.height, 0);
    expect(onPage().top).toBeCloseTo(atDesk.top, 0);
  });

  test("typed near the right edge, its start moves left as it grows, so the whole line sent is on the page", async () => {
    const screen = await openEditor({ doc: RED_DOC });
    const pressed = await doubleClickPaper(screen, [560, 300]);
    const field = screen.getByRole("textbox", { name: "New text" });
    await expect.element(field).toHaveFocus();
    await userEvent.keyboard("Signed by M. Halvorsen");
    // Live, as it's typed: the field has moved in from the press.
    expect(field.element().getBoundingClientRect().left).toBeLessThan(pressed.x - 1);
    await userEvent.keyboard("{Enter}");

    await expect.poll(lastRendered).toHaveLength(1);
    const [sent] = lastRendered();
    const mark = screen.getByRole("button", { name: "Signed by M. Halvorsen" });
    await expect.element(mark).toBeInTheDocument();
    const layer = screen.getByRole("region", { name: "Page 1" }).element().querySelector("img")?.parentElement;
    expect(mark.element().getBoundingClientRect().right).toBeLessThanOrEqual(
      (layer?.getBoundingClientRect().right ?? 0) + 0.5,
    );
    // Undone and redone, it comes back where it was sent.
    await userEvent.keyboard("{Control>}z{/Control}");
    await expect.element(mark).not.toBeInTheDocument();
    await userEvent.keyboard("{Control>}{Shift>}z{/Shift}{/Control}");
    await expect.element(mark).toBeInTheDocument();
    expect(lastRendered()[0]).toEqual(sent);
  });

  test("opened at the bottom edge, its baseline is on the page, and so are its letters' feet", async () => {
    const screen = await openEditor({ doc: RED_DOC });
    await doubleClickPaper(screen, [300, A4.height - 1]);
    await userEvent.keyboard("Signed{Enter}");

    await expect.poll(lastRendered).toHaveLength(1);
    const [sent] = lastRendered();
    const [, baseline = Infinity] = sent?.kind === "insert" ? sent.origin : [];
    expect(baseline).toBeLessThanOrEqual(A4.height - red.size * NEW_TEXT_DESCENT_EM);
  });

  test("a double-click on a span still edits the span", async () => {
    const screen = await openEditor({ doc: RED_DOC });
    const mark = screen.getByRole("button", { name: "was here" });
    await expect.element(mark).toBeInTheDocument();
    await mark.dblClick();

    await expect.element(screen.getByRole("textbox", { name: "Change “was here”" })).toHaveFocus();
    expect(screen.getByRole("textbox", { name: "New text" }).query()).toBeNull();
  });

  test('in a page font a similar one draws, "a similar font" shows in full as the field opens, heard once', async () => {
    const screen = await openEditor({ doc: ARIAL_DOC });
    await doubleClickPaper(screen, [300, 300]);

    await expect.element(screen.getByRole("textbox", { name: "New text" })).toHaveFocus();
    const warning = fill(SENTENCES.substitute, { font: "Liberation Sans Regular" });
    const slip = newTextSlip();
    await expect.element(slip.getByText(warning)).toBeVisible();
    await expect.element(slip.getByText("a similar font", { exact: true })).toBeVisible();
    const status = screen.getByRole("status");
    await expect.element(status).toHaveTextContent(warning);
    const heard = status.element().firstElementChild;
    // Typing a letter it draws changes nothing it warns of: nothing is heard again.
    await userEvent.keyboard("a");
    await expect.element(screen.getByRole("textbox", { name: "New text" })).toHaveValue("a");
    expect(status.element().firstElementChild).toBe(heard);
  });

  test("the bar's eye marks where new text in a similar font is, as it marks a changed line", async () => {
    const screen = await openEditor({ doc: ARIAL_DOC });
    await doubleClickPaper(screen, [300, 300]);
    await userEvent.keyboard("a{Enter}");
    await screen.getByRole("button", { name: "1 change in a similar font" }).click();
    await screen.getByRole("button", { name: "Show the places in Arial on the page" }).click();

    const marked = () => [...document.querySelectorAll<HTMLElement>("[data-shown]")];
    await expect.poll(() => marked().length).toBe(1);
    const mark = screen.getByRole("button", { name: "a", exact: true }).element().getBoundingClientRect();
    const [shown] = marked();
    expect(shown?.getBoundingClientRect()).toMatchObject({ left: mark.left, top: mark.top, width: mark.width });
  });

  test("Cmd+Enter on a span opens new text on the line below it; arrows nudge it a point, Shift+arrows ten", async () => {
    const screen = await openEditor({ doc: RED_DOC });
    const mark = screen.getByRole("button", { name: "was here" });
    await expect.element(mark).toBeInTheDocument();
    mark.element().focus();
    await userEvent.keyboard("{Control>}{Enter}{/Control}");

    const field = screen.getByRole("textbox", { name: "New text" });
    await expect.element(field).toHaveFocus();
    await userEvent.keyboard("Signed{Enter}");
    await expect.poll(lastRendered).toHaveLength(1);
    const [placed] = lastRendered();
    const [x = 0, y = 0] = placed?.kind === "insert" ? placed.origin : [];
    // Lined up with the span's left edge, its baseline a line below the span's.
    expect([x, y]).toEqual([red.bbox.x0, (red.origin[1] ?? 0) + red.size * LINE_BELOW_EM]);

    await expect.element(screen.getByRole("button", { name: "Signed" })).toHaveFocus();
    await userEvent.keyboard("{ArrowRight}");
    await expect.poll(() => lastRendered()[0]).toMatchObject({ origin: [x + 1, y] });
    await userEvent.keyboard("{Shift>}{ArrowDown}{/Shift}");
    await expect.poll(() => lastRendered()[0]).toMatchObject({ origin: [x + 1, y + 10] });
    await expect.element(screen.getByRole("button", { name: "Signed" })).toHaveFocus();
  });

  test("undone with focus on it, new text sends focus to the text nearest it; moved back, it keeps focus", async () => {
    const screen = await openEditor({ doc: RED_DOC });
    await doubleClickPaper(screen, [300, 300]);
    await userEvent.keyboard("Signed{Enter}");
    const mark = screen.getByRole("button", { name: "Signed" });
    await expect.element(mark).toHaveFocus();
    await userEvent.keyboard("{ArrowRight}");
    await expect.poll(() => lastRendered()[0]).toMatchObject({ kind: "insert" });

    // Its move undone, the mark stays where focus is.
    await userEvent.keyboard("{Control>}z{/Control}");
    await expect.element(mark).toHaveFocus();
    // Its adding undone, the mark goes, and focus goes to the line it was set below.
    await userEvent.keyboard("{Control>}z{/Control}");
    await expect.element(mark).not.toBeInTheDocument();
    await expect.element(screen.getByRole("button", { name: "was here" })).toHaveFocus();
  });

  test("on its margin note, Cmd+Z sends focus to the text nearest it as the note goes, as pressing the note does", async () => {
    const screen = await openEditor({ doc: RED_DOC });
    await doubleClickPaper(screen, [300, 300]);
    await userEvent.keyboard("Signed{Enter}");
    const note = screen.getByRole("button", { name: "Undo: take out “Signed”" });
    await expect.element(note).toBeInTheDocument();
    note.element().focus();
    await userEvent.keyboard("{Control>}z{/Control}");

    await expect.element(note).not.toBeInTheDocument();
    await expect.element(screen.getByRole("button", { name: "was here" })).toHaveFocus();
  });

  test("on its margin note, Cmd+Shift+Z that takes it out again sends focus to the text nearest it", async () => {
    const screen = await openEditor({ doc: RED_DOC });
    await doubleClickPaper(screen, [300, 300]);
    await userEvent.keyboard("Signed{Enter}");
    // Typed into again and emptied, it's taken out; undone, it and its note are back.
    await userEvent.keyboard("{Enter}");
    await screen.getByRole("textbox", { name: "Change “Signed”" }).fill("");
    await userEvent.keyboard("{Enter}");
    await expect.element(screen.getByRole("button", { name: "Signed" })).not.toBeInTheDocument();
    await userEvent.keyboard("{Control>}z{/Control}");
    const note = screen.getByRole("button", { name: "Undo: take out “Signed”" });
    await expect.element(note).toBeInTheDocument();
    note.element().focus();
    await userEvent.keyboard("{Control>}{Shift>}z{/Shift}{/Control}");

    await expect.element(note).not.toBeInTheDocument();
    await expect.element(screen.getByRole("button", { name: "was here" })).toHaveFocus();
  });

  test("on its margin note that stays, Cmd+Z sends focus to its mark", async () => {
    const screen = await openEditor({ doc: RED_DOC });
    await doubleClickPaper(screen, [300, 300]);
    await userEvent.keyboard("Signed{Enter}");
    await userEvent.keyboard("{Enter}");
    await screen.getByRole("textbox", { name: "Change “Signed”" }).fill("Signed M.");
    await userEvent.keyboard("{Enter}");
    const note = screen.getByRole("button", { name: "Undo: take out “Signed M.”" });
    await expect.element(note).toBeInTheDocument();
    note.element().focus();
    await userEvent.keyboard("{Control>}z{/Control}");

    await expect.element(screen.getByRole("button", { name: "Undo: take out “Signed”" })).toBeVisible();
    await expect.element(screen.getByRole("button", { name: "Signed" })).toHaveFocus();
  });

  test("on a far page's margin note, Cmd+Z taking out new text brings the text nearest it into view, with focus", async () => {
    const far = aSpan({ id: "far", text: "far text", page: 7, bbox: { x0: 72, y0: 100, x1: 152, y1: 124 } });
    const doc = aDoc({ ...RED_DOC, spans: [red, far], pages: Array.from({ length: 8 }, () => A4) });
    const screen = await openEditor({ doc });
    screen.getByRole("region", { name: "Page 8" }).element().scrollIntoView();
    const farMark = screen.getByRole("button", { name: "far text" });
    await expect.element(farMark).toBeInTheDocument();
    farMark.element().focus();
    await userEvent.keyboard("{Enter}");
    await screen.getByRole("textbox").fill("far texts");
    await userEvent.keyboard("{Enter}");
    window.scrollTo(0, 0);
    await doubleClickPaper(screen, [300, 300]);
    await userEvent.keyboard("Signed{Enter}");
    await expect.element(screen.getByRole("button", { name: "Undo: take out “Signed”" })).toBeInTheDocument();
    // The new text, the last thing done, undone from the far change's note, which stays.
    screen.getByRole("region", { name: "Page 8" }).element().scrollIntoView();
    const note = screen.getByRole("button", { name: "Undo: “far texts” goes back to “far text”" });
    await expect.element(note).toBeInTheDocument();
    note.element().focus();
    await userEvent.keyboard("{Control>}z{/Control}");

    await expect.element(screen.getByRole("button", { name: "Signed" })).not.toBeInTheDocument();
    await expect.element(screen.getByRole("button", { name: "was here" })).toHaveFocus();
  });

  test("pointed at, new text's note says what it is and how to move it, in the server's words", async () => {
    const screen = await openEditor({ doc: RED_DOC });
    await doubleClickPaper(screen, [300, 300]);
    await userEvent.keyboard("Signed{Enter}");
    const mark = screen.getByRole("button", { name: "Signed" });
    await expect.element(mark).toHaveFocus();
    const note = screen.getByRole("tooltip");
    await expect.element(note).toHaveTextContent(`${SENTENCES.new_text}${SENTENCES.new_text_moves}`);
  });

  test("Escape closes new text's note keyboard focus showed, until focus leaves it (WCAG 1.4.13, Dismissible)", async () => {
    const screen = await openEditor({ doc: RED_DOC });
    await doubleClickPaper(screen, [300, 300]);
    await userEvent.keyboard("Signed{Enter}");
    const mark = screen.getByRole("button", { name: "Signed" });
    await expect.element(mark).toHaveFocus();
    const note = screen.getByRole("tooltip");
    await expect.element(note).toBeVisible();
    await userEvent.keyboard("{Escape}");
    await expect.element(note).not.toBeInTheDocument();
    await expect.element(mark).toHaveFocus();
    await userEvent.keyboard("{Shift>}{Tab}{/Shift}{Tab}");
    await expect.element(mark).toHaveFocus();
    await expect.element(note).toBeVisible();
  });

  test("Escape over new text's note a hover showed leaves the next keyboard focus on it to show it", async () => {
    const screen = await openEditor({ doc: RED_DOC });
    await doubleClickPaper(screen, [300, 300]);
    await userEvent.keyboard("Signed{Enter}");
    const mark = screen.getByRole("button", { name: "Signed" });
    await expect.element(mark).toHaveFocus();
    await userEvent.keyboard("{Shift>}{Tab}{/Shift}");
    const before = document.activeElement as HTMLElement;
    await mark.hover();
    await expect.element(mark).toHaveAttribute("aria-describedby");
    await userEvent.keyboard("{Escape}");
    await expect.element(mark).not.toHaveAttribute("aria-describedby");
    await screen.getByRole("region", { name: "Page 1" }).hover({ position: { x: 4, y: 4 } });
    before.focus();
    await userEvent.keyboard("{Tab}");
    await expect.element(mark).toHaveFocus();
    await expect.element(mark).toHaveAttribute("aria-describedby");
  });

  test("the pointer passing over new text's note keyboard focus showed leaves it shown (WCAG 1.4.13, Persistent)", async () => {
    const screen = await openEditor({ doc: RED_DOC });
    await doubleClickPaper(screen, [300, 300]);
    await userEvent.keyboard("Signed{Enter}");
    const mark = screen.getByRole("button", { name: "Signed" });
    await expect.element(mark).toHaveFocus();
    const note = screen.getByRole("tooltip");
    await expect.element(note).toBeVisible();
    await Promise.all(document.getAnimations().map((animation) => animation.finished));
    await note.hover();
    await screen.getByRole("region", { name: "Page 1" }).hover({ position: { x: 4, y: 4 } });
    const leftAt = performance.now();
    // Past the delay a note closes after: still there, focus still on its mark.
    await expect
      .poll(() => performance.now() - leftAt, { timeout: NOTE_CLOSE_DELAY_MS * 4 })
      .toBeGreaterThan(NOTE_CLOSE_DELAY_MS * 2);
    await expect.element(mark).toHaveFocus();
    await expect.element(note).toBeVisible();
  });

  test("with a reading persisted before new text's sentences were sent, its note and its margin note say nothing of it", async () => {
    const older = { ...RED_DOC.sentences };
    for (const key of ["new_text", "new_text_moves", "new_text_moves_touch"] as const) {
      Reflect.deleteProperty(older, key);
    }
    vi.mocked(documentOf)
      .mockReset()
      .mockReturnValue(new Promise(() => undefined));
    const persisted = { work: { history: EMPTY_HISTORY, fonts: [] }, until: Date.now() + 60_000 };
    const screen = await openEditor({ doc: { ...RED_DOC, sentences: older }, persisted });
    await doubleClickPaper(screen, [300, 300]);
    await userEvent.keyboard("Signed{Enter}");
    await expect.element(screen.getByRole("button", { name: "Signed" })).toHaveFocus();
    expect(screen.getByRole("tooltip").query()).toBeNull();
    expect(document.body.textContent).not.toContain(SENTENCES.new_text);
  });

  test("on a touch screen, a tap on new text shows its note in a finger's words, and a second tap types in it", async () => {
    const screen = await openEditor({ doc: RED_DOC });
    await doubleClickPaper(screen, [300, 300]);
    await userEvent.keyboard("Signed{Enter}");
    const mark = screen.getByRole("button", { name: "Signed" });
    await expect.element(mark).toHaveFocus();
    mark.element().blur();
    await emulateTouchScreen();
    const box = mark.element().getBoundingClientRect();
    const at = { x: box.left + box.width / 2, y: box.top + box.height / 2 };
    const tap = async () => {
      await cdp().send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [at] });
      await cdp().send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
    };

    await tap();
    const note = screen.getByRole("tooltip");
    await expect.element(note).toHaveTextContent(`${SENTENCES.new_text}${SENTENCES.new_text_moves_touch}`);
    expect(screen.getByRole("textbox", { name: "Change “Signed”" }).query()).toBeNull();
    await tap();
    await expect.element(screen.getByRole("textbox", { name: "Change “Signed”" })).toHaveFocus();
  });

  // Each way a drag is cut short: the browser taking the pointer back, the mark losing it, the window losing focus.
  const CANCELS: [string, (mark: Element, at: PointerEventInit) => void][] = [
    ["the browser cancels", (mark, at) => mark.dispatchEvent(new PointerEvent("pointercancel", at))],
    ["whose mark loses the pointer", (mark, at) => mark.dispatchEvent(new PointerEvent("lostpointercapture", at))],
    ["the window's losing focus cuts short", () => window.dispatchEvent(new FocusEvent("blur"))],
  ];

  test.each(CANCELS)("a drag %s puts the text back, and the next click and drag work", async (_, cancel) => {
    const screen = await openEditor({ doc: RED_DOC });
    await doubleClickPaper(screen, [300, 300]);
    await userEvent.keyboard("Signed{Enter}");
    await expect.poll(lastRendered).toHaveLength(1);
    const sentX = () => {
      const [sent] = lastRendered();
      return sent?.kind === "insert" ? sent.origin[0] : undefined;
    };
    const x = sentX() ?? 0;
    const rendered = vi.mocked(renderStrips).mock.calls.length;
    const mark = screen.getByRole("button", { name: "Signed" });
    await expect.element(mark).toBeInTheDocument();
    const from = mark.element().getBoundingClientRect();
    const pointer = { pointerId: 7, pointerType: "mouse", isPrimary: true, button: 0, bubbles: true };
    const at = (dx: number) => ({ ...pointer, clientX: from.left + 2 + dx, clientY: from.top + 2 + dx });
    mark.element().dispatchEvent(new PointerEvent("pointerdown", at(0)));
    mark.element().dispatchEvent(new PointerEvent("pointermove", at(40)));
    await expect.poll(() => mark.element().getBoundingClientRect().left).toBeGreaterThan(from.left + 20);

    cancel(mark.element(), at(40));
    await expect.poll(() => mark.element().getBoundingClientRect().left).toBe(from.left);
    expect(vi.mocked(renderStrips).mock.calls.length).toBe(rendered);
    // The next press is a press, not a drag's end.
    await mark.click();
    await expect.element(screen.getByRole("textbox", { name: "Change “Signed”" })).toHaveFocus();
    await userEvent.keyboard("{Escape}");
    await expect.element(mark).toHaveFocus();
    // The next drag moves it, from where it was.
    mark.element().dispatchEvent(new PointerEvent("pointerdown", at(0)));
    mark.element().dispatchEvent(new PointerEvent("pointermove", at(30)));
    mark.element().dispatchEvent(new PointerEvent("pointerup", at(30)));
    await expect.poll(sentX).toBeGreaterThan(x);
  });

  /** New text set just above "was here", grabbed at its left end and dragged onto that line, which is drawn over it. */
  async function dragOntoTheLine(screen: Awaited<ReturnType<typeof render>>, pointerType: string) {
    await doubleClickPaper(screen, [80, 60]);
    await userEvent.keyboard("Signed M. Halvorsen{Enter}");
    await expect.poll(lastRendered).toHaveLength(1);
    const mark = screen.getByRole("button", { name: "Signed M. Halvorsen" });
    await expect.element(mark).toBeInTheDocument();
    const from = mark.element().getBoundingClientRect();
    const line = screen.getByRole("button", { name: "was here" }).element().getBoundingClientRect();
    const pointer = { pointerId: 7, pointerType, isPrimary: true, button: 0, bubbles: true };
    const grab = { ...pointer, clientX: from.left + 4, clientY: (from.top + from.bottom) / 2 };
    const over = { ...grab, clientY: (line.top + line.bottom) / 2 };
    mark.element().dispatchEvent(new PointerEvent("pointerdown", grab));
    mark.element().dispatchEvent(new PointerEvent("pointermove", over));
    await expect.poll(() => mark.element().getBoundingClientRect().top).toBeGreaterThan(from.top + 20);
    // What the browser aims the pointer's events at: the line, drawn after the new text above it.
    const under = document.elementFromPoint(over.clientX, over.clientY);
    expect(under?.textContent).toBe("was here");
    return { mark, from, under, over };
  }

  // The next press after a drop the mark never heard: a click on it, or Enter.
  const PRESSES: [string, (mark: HTMLElement | SVGElement) => Promise<void>][] = [
    ["a click", (mark) => userEvent.click(mark)],
    [
      "Enter",
      async (mark) => {
        mark.focus();
        await userEvent.keyboard("{Enter}");
      },
    ],
  ];

  test.each(PRESSES)("new text let go over a line drawn on it moves, and %s next types into it", async (_, press) => {
    const screen = await openEditor({ doc: RED_DOC });
    const { mark, under, over } = await dragOntoTheLine(screen, "mouse");
    const sentY = () => {
      const [sent] = lastRendered();
      return sent?.kind === "insert" ? sent.origin[1] : undefined;
    };
    const y = sentY() ?? 0;
    // Let go over the line, as outside the window: no press on the mark ends the drag.
    under?.dispatchEvent(new PointerEvent("pointerup", over));
    await expect.poll(sentY).toBeGreaterThan(y + 20);

    await press(mark.element());
    await expect.element(screen.getByRole("textbox", { name: "Change “Signed M. Halvorsen”" })).toHaveFocus();
  });

  test("a drag the browser cancels over a line drawn on it puts the text back", async () => {
    const screen = await openEditor({ doc: RED_DOC });
    const { mark, from, under, over } = await dragOntoTheLine(screen, "touch");
    const rendered = vi.mocked(renderStrips).mock.calls.length;
    // The browser aims its pointercancel at what's under the finger: the line, not the mark.
    under?.dispatchEvent(new PointerEvent("pointercancel", over));
    await expect.poll(() => mark.element().getBoundingClientRect().top).toBe(from.top);
    expect(vi.mocked(renderStrips).mock.calls.length).toBe(rendered);
  });

  test("a typed signature is in a handwriting face a step larger, chosen from the chip, and its margin note takes it out", async () => {
    const screen = await openEditor({ doc: RED_DOC });
    await doubleClickPaper(screen, [300, 300]);
    await userEvent.keyboard("M. Halvorsen");
    await screen.getByRole("option", { name: "Caveat Bold" }).click();
    // A pointer's choice leaves the typing in the field.
    await expect.element(screen.getByRole("textbox", { name: "New text" })).toHaveFocus();
    await expect.element(screen.getByText("26 pt")).toBeVisible();
    await userEvent.keyboard("{Enter}");

    await expect.poll(() => lastRendered()[0]).toMatchObject({ font: "Caveat Bold", size: 26 });
    expect(lastRendered()[0]).not.toHaveProperty("font_object");
    await screen.getByRole("button", { name: "Undo: take out “M. Halvorsen”" }).click();
    await expect.element(screen.getByRole("button", { name: "M. Halvorsen" })).not.toBeInTheDocument();
    await expect.element(screen.getByRole("banner").getByText(/change/)).not.toBeInTheDocument();
  });

  test("Tab from the fonts goes on to the page's next line, and the typing is kept", async () => {
    const screen = await openEditor({ doc: RED_DOC });
    await doubleClickPaper(screen, [300, 60]); // above the page's one line
    await userEvent.keyboard("Signed{Tab}");
    await expect.element(screen.getByRole("option", { name: "Times Roman" })).toHaveFocus();
    await userEvent.keyboard("{Tab}");
    await expect.element(screen.getByRole("button", { name: red.text, exact: true })).toHaveFocus();
    await expect.element(screen.getByRole("button", { name: "Signed", exact: true })).toBeInTheDocument();
  });

  test("Tab from the fonts with nothing after it on the page goes where Tab from its place would", async () => {
    const screen = await openEditor({ doc: { ...RED_DOC, pages: [A4, A4] } });
    await doubleClickPaper(screen, [300, 300]); // below the page's one line
    await userEvent.keyboard("Signed{Tab}");
    await expect.element(screen.getByRole("option", { name: "Times Roman" })).toHaveFocus();
    await userEvent.keyboard("{Tab}");
    // Past the page, before the next: as Tab goes on from its last line.
    await expect.element(screen.getByRole("button", { name: "Split after page 1" })).toHaveFocus();
    await expect.element(screen.getByRole("button", { name: "Signed", exact: true })).toBeInTheDocument();
  });

  test("in its fonts, undo and redo are the typing's, not the window's or the browser's: nothing is undone", async () => {
    const screen = await openEditor({ doc: RED_DOC });
    await changeSpan(screen, "is done");
    await doubleClickPaper(screen, [300, 300]);
    await userEvent.keyboard("Signed{Tab}");
    const chosen = screen.getByRole("option", { name: "Times Roman" });
    await expect.element(chosen).toHaveFocus();

    await userEvent.keyboard("{Control>}z{/Control}");
    await userEvent.keyboard("{Control>}{Shift>}z{/Shift}{/Control}");
    await expect.element(chosen).toHaveFocus();
    await expect.element(screen.getByRole("textbox", { name: "New text" })).toHaveValue("Signed");
    expect(screen.getByRole("button", { name: "is done" }).query()).not.toBeNull();
  });
});

describe("new text on a page with no text", () => {
  test("Ctrl+Enter on its tick, or on its delete button, opens new text there and deletes nothing", async () => {
    const screen = await openEditor({ doc: { ...DOC, pages: [A4, A4] } });
    screen.getByRole("navigation", { name: "Pages" }).getByRole("link", { name: "Page 2" }).element().focus();
    await userEvent.keyboard("{Control>}{Enter}{/Control}");
    const field = screen.getByRole("textbox", { name: "New text" });
    await expect.element(field).toHaveFocus();

    // Put away, focus goes to the page's own control: its delete button, which opens it the same way.
    await userEvent.keyboard("{Escape}");
    await expect.element(screen.getByRole("button", { name: "Delete page 2", exact: true })).toHaveFocus();
    await userEvent.keyboard("{Control>}{Enter}{/Control}");
    await expect.element(field).toHaveFocus();
    expect(screen.getByRole("main").getByText("This page is left out of the download").query()).toBeNull();
  });

  // The only page left can't be deleted, so its delete button can't take focus: its tick does.
  const BARE = aDoc({ spans: [], fonts: [] });
  const tickOne = (screen: Awaited<ReturnType<typeof render>>) =>
    screen.getByRole("navigation", { name: "Pages" }).getByRole("link", { name: "Page 1" });

  test("on the only page, put away with Escape, new text sends focus to the page's tick", async () => {
    const screen = await openEditor({ doc: BARE });
    await doubleClickPaper(screen, [300, 300]);
    await expect.element(screen.getByRole("textbox", { name: "New text" })).toHaveFocus();
    await userEvent.keyboard("{Escape}");

    await expect.element(tickOne(screen)).toHaveFocus();
  });

  test("on the only page, undone with focus on it, new text sends focus to the page's tick", async () => {
    const screen = await openEditor({ doc: BARE });
    await doubleClickPaper(screen, [300, 300]);
    await userEvent.keyboard("Signed{Enter}");
    const mark = screen.getByRole("button", { name: "Signed" });
    await expect.element(mark).toHaveFocus();
    await userEvent.keyboard("{Control>}z{/Control}");

    await expect.element(mark).not.toBeInTheDocument();
    await expect.element(tickOne(screen)).toHaveFocus();
  });

  test("on the only page, taken out from its margin note, new text sends focus to the page's tick", async () => {
    const screen = await openEditor({ doc: BARE });
    await doubleClickPaper(screen, [300, 300]);
    await userEvent.keyboard("Signed{Enter}");
    const note = screen.getByRole("button", { name: "Undo: take out “Signed”" });
    await expect.element(note).toBeInTheDocument();
    note.element().focus();
    await userEvent.keyboard("{Enter}");

    await expect.element(screen.getByRole("button", { name: "Signed" })).not.toBeInTheDocument();
    await expect.element(tickOne(screen)).toHaveFocus();
  });

  test("on the only page, opened from its tick, new text gives focus back to the tick, put away or undone", async () => {
    const screen = await openEditor({ doc: BARE });
    const tick = tickOne(screen);
    await expect.element(tick).toBeInTheDocument();
    tick.element().focus();
    await userEvent.keyboard("{Control>}{Enter}{/Control}");
    await expect.element(screen.getByRole("textbox", { name: "New text" })).toHaveFocus();
    await userEvent.keyboard("{Escape}");
    await expect.element(tick).toHaveFocus();

    await userEvent.keyboard("{Control>}{Enter}{/Control}");
    await userEvent.keyboard("Signed{Enter}");
    await expect.element(screen.getByRole("button", { name: "Signed" })).toHaveFocus();
    await userEvent.keyboard("{Control>}z{/Control}");
    await expect.element(tick).toHaveFocus();
  });
});

describe("new text in a face we ship", () => {
  test("a signature whose face lacks a letter is in a similar font as it's typed, and the bar counts it so", async () => {
    const screen = await openEditor({ doc: DOC });
    await doubleClickPaper(screen, [300, 300]);
    await userEvent.keyboard("Ω Smith");
    await screen.getByRole("option", { name: "Caveat Bold" }).click();
    // Caveat has no Ω: a face that has it draws the whole line.
    await expect.element(newTextSlip().getByText("Noto Sans Bold", { exact: false })).toBeVisible();
    await userEvent.keyboard("{Enter}");

    const count = screen.getByRole("button", { name: "1 change in a similar font" });
    await expect.element(count).toBeVisible();
    await count.click();
    const row = screen.getByRole("dialog").getByText("Caveat Bold", { exact: true });
    await expect.element(row).toBeVisible();
    await expect.element(screen.getByRole("dialog").getByText(/Noto Sans Bold/)).toBeVisible();
  });

  test("a signature its face draws whole is exact: no warning, and none counted", async () => {
    const screen = await openEditor({ doc: DOC });
    await doubleClickPaper(screen, [300, 300]);
    await userEvent.keyboard("M. Smith");
    await screen.getByRole("option", { name: "Caveat Bold" }).click();
    await expect.element(screen.getByText("looks identical")).toBeVisible();
    await userEvent.keyboard("{Enter}");

    await expect.element(screen.getByRole("banner").getByText("1 change", { exact: true })).toBeVisible();
  });
});

describe("how to start", () => {
  /** The how-to-start line `words` as shown on screen, of the one beside the first page and the one above it. */
  const shownLine = (words: string) =>
    [...document.querySelectorAll("p > span")].find((line) => line.textContent === words && line.checkVisibility());
  const sheetOne = (screen: Awaited<ReturnType<typeof render>>) =>
    screen.getByRole("region", { name: "Page 1" }).element().querySelector("[style*='aspect-ratio']");

  test("the first page's margin says how, before any change, and the note goes with the first edit", async () => {
    const [wide, tall] = [window.innerWidth, window.innerHeight];
    await page.viewport(1400, 900);
    try {
      const screen = await openEditor();
      await expect.poll(() => shownLine(SENTENCES.how_to_fix)).toBeDefined();
      // Beside the page, in its margin, not on it.
      const words = shownLine(SENTENCES.how_to_add)?.getBoundingClientRect();
      expect(words?.right).toBeLessThanOrEqual(sheetOne(screen)?.getBoundingClientRect().left ?? 0);

      await changeSpan(screen, "is here");
      await expect.poll(() => shownLine(SENTENCES.how_to_add)).toBeUndefined();
    } finally {
      await page.viewport(wide, tall);
    }
  });

  test("a reading persisted before its sentences were sent shows no note, squid and all", async () => {
    const older = { ...SENTENCES };
    for (const key of ["how_to_fix", "how_to_add", "how_to_fix_touch", "how_to_add_touch"] as const) {
      Reflect.deleteProperty(older, key);
    }
    vi.mocked(documentOf)
      .mockReset()
      .mockReturnValue(new Promise(() => undefined));
    const persisted = { work: { history: EMPTY_HISTORY, fonts: [] }, until: Date.now() + 60_000 };
    const screen = await openEditor({ doc: { ...DOC, sentences: older }, persisted });
    await expect.element(screen.getByRole("button", { name: "was here" })).toBeInTheDocument();
    expect(document.querySelector("[class*='beside'], [class*='above']")).toBeNull();
  });

  test("with a mouse it says to click and double-click; on a touch screen, to tap and double-tap", async () => {
    const [wide, tall] = [window.innerWidth, window.innerHeight];
    await page.viewport(1400, 900);
    try {
      await openEditor();
      await expect.poll(() => shownLine(SENTENCES.how_to_add)).toBeDefined();
      expect(shownLine(SENTENCES.how_to_fix)).toBeDefined();
      expect(shownLine(SENTENCES.how_to_fix_touch)).toBeUndefined();
      expect(shownLine(SENTENCES.how_to_add_touch)).toBeUndefined();
      await emulateTouchScreen();
      await expect.poll(() => shownLine(SENTENCES.how_to_add_touch)).toBeDefined();
      expect(shownLine(SENTENCES.how_to_fix_touch)).toBeDefined();
      expect(shownLine(SENTENCES.how_to_fix)).toBeUndefined();
      expect(shownLine(SENTENCES.how_to_add)).toBeUndefined();
    } finally {
      await page.viewport(wide, tall);
    }
  });

  test("on a narrow screen, where there's no margin, the same line sits above the first page", async () => {
    const [wide, tall] = [window.innerWidth, window.innerHeight];
    await page.viewport(390, 844);
    try {
      const screen = await openEditor();
      await expect.poll(() => shownLine(SENTENCES.how_to_add)).toBeDefined();
      const above = shownLine(SENTENCES.how_to_add)?.getBoundingClientRect().bottom ?? Infinity;
      expect(above).toBeLessThanOrEqual(sheetOne(screen)?.getBoundingClientRect().top ?? 0);
    } finally {
      await page.viewport(wide, tall);
    }
  });
});
