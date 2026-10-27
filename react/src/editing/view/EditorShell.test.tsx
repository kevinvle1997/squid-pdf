import { beforeEach, describe, expect, test, vi } from "vitest";
import { page, userEvent } from "vitest/browser";
import { render } from "vitest-browser-react";
import "../../styles/tokens.css";
import "../../styles/base.css";
import SOME_FONT from "../../../../src/squidpdf/fonts/LiberationSans-Regular.ttf?url";
import { documentOf, exportPdf, ProblemError, putFont, renderStrips, stillThere, upload } from "../../api/client";
import type { Document, Render } from "../../api/types";
import { type PersistedWork, persistFile, persistState } from "../../documents/localPersistence";
import {
  A4,
  aDoc,
  aDocumentNotice,
  aFit,
  aFont,
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
import { EXPORT_INTENT_MS } from "../constants";
import { EMPTY_HISTORY, historyReducer } from "../history";
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
}));

const span = aSpan({ id: "s1", text: "was here", size: 20, bbox: { x0: 72, y0: 100, x1: 152, y1: 124 } });
const DOC = aDoc({ spans: [span], fonts: [aFont("Times-Roman")] });

// Arial only named: a similar font stands in, and its letters are the ones the page lists.
const LETTERS = halfWideGlyphs("abcdefghijklmnopqrstuvwxyz ");
const NAMED = aFont("Arial", { substitute: "Liberation Sans Regular", glyphs: LETTERS });
const ARIAL_SPAN = aSpan({ ...span, font: "Arial", fidelity: "substitute", substitute: "Liberation Sans Regular" });
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
    // The bar's own warning colour, which reads on its ink.
    const warn = bar.element().appendChild(document.createElement("span"));
    warn.style.color = "var(--warn)";
    expect(getComputedStyle(bar.getByText("1", { exact: true }).element()).color).toBe(getComputedStyle(warn).color);
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

  test("after a change, it counts the lines left long past their room, warned", async () => {
    const tooLong = { code: "too_long", params: { percent: 12 } };
    vi.mocked(renderStrips).mockResolvedValue(
      aReply({ fits: { [span.id]: aFit({ delta_pt: 10, message: "about 12% too long", message_parts: [tooLong] }) } }),
    );
    const screen = await openEditor();
    await changeSpan(screen, "is here and there");
    const long = screen.getByRole("banner").getByText("1 too long");
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
    await expect.element(screen.getByRole("status")).toHaveTextContent("Redacted was here");

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
    await expect.element(status).toHaveTextContent("Back to was here");
    const firstAnnouncement = status.element().firstElementChild;

    await userEvent.keyboard("{Control>}{Shift>}z{/Shift}{/Control}");
    await expect.element(status).toHaveTextContent("Changed to is here");
    const focused = document.activeElement;
    window.scrollTo(0, 0);
    await userEvent.keyboard("{Control>}z{/Control}");

    await expect.element(status).toHaveTextContent("Back to was here");
    expect(status.element().firstElementChild).not.toBe(firstAnnouncement);
    await vi.waitFor(() => expect(window.scrollY).toBeGreaterThan(0));
    expect(document.activeElement).toBe(focused);
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
    // The file's own Times draws it, turned on the page, and has no Y.
    const turned = aSpan({ ...span, fidelity: "approximate", why: { code: "turned_text", params: {} } });
    const own = aDoc({ spans: [turned], fonts: [aFont("Times-Roman", { glyphs: LETTERS })] });
    const screen = await openEditor({ doc: own, name: "letter.pdf" });
    await typeInSpan(screen, "was Yes");

    const offer = screen.getByRole("button", { name: "Use your copy" });
    const why = screen.getByText(SENTENCES.approximate.turned_text ?? "", { exact: false }).first();
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
    fonts: [aFont("Times-Roman")],
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
