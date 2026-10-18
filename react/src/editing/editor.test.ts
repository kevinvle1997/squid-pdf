import { beforeEach, describe, expect, test, vi } from "vitest";
import { exportPdf, ProblemError, renderStrips } from "../api/client";
import type { Render } from "../api/types";
import { A4, aDoc, aFileNotice, aFont, aProblem, aSpan, COPY } from "../fixtures";
import { changedCount, createEditor, type Editor, layoutOf, putBack, redo, undo, warnedCount } from "./editor";
import { exportNow, partName, splitAfter, splitPages, unexported } from "./export";
import { plain, warn } from "./notices";
import { quietFont } from "./quiet";
import { edit, enter, fieldFocused, finish, stepWayOut, troublesIn, typeInto } from "./typing";
import { fill } from "./words";

vi.mock(import("../api/client"), async (original) => ({
  ...(await original()),
  renderStrips: vi.fn(),
  exportPdf: vi.fn(),
  upload: vi.fn(),
  stillThere: vi.fn(),
}));

const DOC = aDoc({
  spans: [
    aSpan({ id: "own", font: "Kept" }),
    aSpan({ id: "substituted", font: "Named", fidelity: "substitute", substitute: "Liberation Serif Regular" }),
  ],
  fonts: [aFont("Kept"), aFont("Named", { substitute: "Liberation Serif Regular" })],
});
const FILE = new File(["%PDF-"], "contract.pdf");

let editor: Editor;

beforeEach(() => {
  vi.mocked(renderStrips)
    .mockReset()
    .mockReturnValue(new Promise<Render>(() => undefined));
  vi.mocked(exportPdf).mockReset();
  editor = createEditor(FILE, DOC, 2);
});

const typed = (spanId: string, text: string) => {
  edit(editor, spanId, null);
  typeInto(editor, text);
  finish(editor, true);
};

describe("an edit", () => {
  test("as its field takes focus, what an edit there warns of is heard, until its font is quieted", () => {
    // Its font has every letter it reads, so the warning is all there is to hear.
    const glyphs = Object.fromEntries([..."was similar"].map((letter) => [letter, 500]));
    const similar = aSpan({
      id: "similar",
      font: "Named",
      fidelity: "substitute",
      substitute: "Liberation Serif Regular",
    });
    const fonts = [aFont("Named", { substitute: "Liberation Serif Regular", glyphs })];
    editor = createEditor(FILE, aDoc({ spans: [similar], fonts }), 2);
    edit(editor, "similar", null);
    fieldFocused(editor);
    expect(editor.store.get().said.text).toBe(fill(COPY.substitute, { font: "Liberation Serif Regular" }));
    finish(editor, false);
    quietFont(editor, "Named", { quiet: true });
    const quieted = editor.store.get().said;
    edit(editor, "similar", null);
    fieldFocused(editor);
    expect(editor.store.get().said).toBe(quieted);
  });

  test("typing holds a draft apart from the history, until it's finished", () => {
    edit(editor, "own", null);
    expect(editor.store.get().draft).toMatchObject({ spanId: "own", page: 0, text: "was own" });
    typeInto(editor, "now");
    expect(editor.store.get().history.done).toEqual([]);
    finish(editor, true);
    expect(editor.store.get().draft).toBeNull();
    expect(editor.store.get().reading.spans.get("own")?.text).toBe("now");
    expect(editor.store.get().said.text).toBe("Changed to now");
  });

  test("a trouble is said as it appears or changes, not as its numbers tick by with each letter", () => {
    const glyphs = { a: 500, b: 500 }; // 5 pt each at 10 pt
    const doc = aDoc({ spans: [aSpan({ id: "ab", text: "ab" })], fonts: [aFont("Times-Roman", { glyphs })] });
    editor = createEditor(FILE, doc, 2);
    edit(editor, "ab", null);
    typeInto(editor, "abab");
    expect(editor.store.get().said.text).toBe("10.0 pt too long");
    typeInto(editor, "ababa");
    expect(editor.store.get().said.text).toBe("10.0 pt too long");
    typeInto(editor, "ababac");
    expect(editor.store.get().said.text).toContain("no c in this font");
  });

  test("Up or Down chooses how a long line fits, and the edit takes it while it's offered", () => {
    // Twenty letters at 5 pt each is 100 pt; one more runs 5 pt past, where every way out is offered.
    const glyphs = Object.fromEntries([..."abcdefghijklmnopqrstuvwxyz"].map((letter) => [letter, 500]));
    const long = aSpan({ id: "long", text: "abcdefghijklmnopqrst" });
    editor = createEditor(FILE, aDoc({ spans: [long], fonts: [aFont("Times-Roman", { glyphs })] }), 2);
    edit(editor, "long", null);
    typeInto(editor, `${long.text}u`);
    expect(editor.store.get().said.text).toBe("5.0 pt too long; Up or Down chooses how it fits");

    // From leaving it long, the last, round to the first.
    expect(stepWayOut(editor, 1)).toBe(true);
    expect(editor.store.get().said.text).toBe(COPY.options.shrink?.label);
    expect(troublesIn(editor.store.get(), `${long.text}u`).said).toEqual([]);
    finish(editor, true);
    expect(editor.store.get().reading.edits).toEqual([
      { kind: "replace", span_id: "long", text: `${long.text}u`, strategy: "shrink" },
    ]);

    // Far longer, only leaving it long is offered: nothing to choose, and the edit is drawn as typed.
    edit(editor, "long", null);
    typeInto(editor, `${long.text}uvwxyz`);
    expect(stepWayOut(editor, 1)).toBe(false);
    finish(editor, true);
    expect(editor.store.get().reading.edits.at(-1)).toEqual({
      kind: "replace",
      span_id: "long",
      text: `${long.text}uvwxyz`,
    });
  });

  test("typing into text a form field draws says at once that an edit here is left out", () => {
    editor = createEditor(
      FILE,
      aDoc({ spans: [aSpan({ id: "field", form_field: true })], fonts: [aFont("Times-Roman")] }),
      2,
    );
    edit(editor, "field", null);
    expect(troublesIn(editor.store.get(), "was field").said).toEqual([COPY.form_field_not_edited]);
  });

  test("a pasted tab or line separator becomes a space, and a bidi control is dropped, since the server refuses new text with one", () => {
    // As pasted: a chat wraps a phone number in bidi controls; Pages breaks lines with U+2028.
    typed("own", "\u202A+1 555\t0100\u202C Invoices are\u2028due\u2029by\u0085May");
    expect(editor.store.get().reading.spans.get("own")?.text).toBe("+1 555 0100 Invoices are due by May");
  });

  test("emptied, the field says it can't be, and Enter keeps it open; Escape still leaves it as it was", () => {
    edit(editor, "own", null);
    typeInto(editor, "  ");
    expect(editor.store.get().said.text).toBe(COPY.empty);
    expect(troublesIn(editor.store.get(), "  ").said).toEqual([COPY.empty]);
    const said = editor.store.get().said;
    enter(editor);
    expect(editor.store.get().draft?.text).toBe("  ");
    // Said again, for a screen reader pressing Enter: the field stays.
    expect(editor.store.get().said.text).toBe(COPY.empty);
    expect(editor.store.get().said.count).not.toBe(said.count);
    typeInto(editor, "now");
    enter(editor);
    expect(editor.store.get().draft).toBeNull();
    expect(editor.store.get().reading.spans.get("own")?.text).toBe("now");
    expect(editor.store.get().focusTo).toEqual({ spanId: "own" });
  });

  test("Escape, the same words, or nothing at all put nothing in the history", () => {
    edit(editor, "own", null);
    typeInto(editor, "now");
    finish(editor, false);
    typed("own", "was own");
    typed("own", "   ");
    expect(editor.store.get().history.done).toEqual([]);
  });

  test("a change goes to the server to draw; what export, reopening and a drop said goes, the document's stays", () => {
    const found = { code: "signed", params: {}, type: "t", detail: "This file is signed." };
    editor = createEditor(FILE, { ...DOC, notices: [found] }, 2);
    const { notices } = editor.store.get();
    const drop = warn("Only a PDF can be dropped here.");
    editor.store.set({
      notices: { ...notices, export: [plain("Downloaded contract.pdf.")], reopen: plain("Opened."), drop },
    });
    typed("own", "now");
    expect(editor.store.get().notices).toEqual({
      document: [warn(found.detail)],
      export: [],
      reopen: null,
      font: null,
      drop: null,
    });
    expect(renderStrips).toHaveBeenCalledTimes(1);
  });

  test("a render that fails is heard as well as shown, and the same failure again isn't said twice", async () => {
    const down = "We can't work on files right now. Try again in a few minutes.";
    vi.mocked(renderStrips).mockRejectedValue(new ProblemError(aProblem(503, down)));
    typed("own", "now");
    await vi.waitFor(() => expect(editor.store.get().said.text).toBe(down));

    typed("own", "then");
    const failedOnce = editor.store.get().drawn;
    await vi.waitFor(() => expect(editor.store.get().drawn).not.toBe(failedOnce));

    expect(editor.store.get().said.text).toBe("Changed to then");
  });

  test("undo and redo say what the span reads now and bring it into view, and focus stays where it is", () => {
    typed("own", "one");
    typed("own", "two");
    undo(editor);
    expect(editor.store.get().reading.spans.get("own")?.text).toBe("one");
    expect(editor.store.get().said.text).toBe("Back to one");
    expect(editor.store.get().scrollTo?.spanId).toBe("own");
    expect(editor.store.get().focusTo).toBeNull();
    redo(editor);
    expect(editor.store.get().said.text).toBe("Changed to two");
    undo(editor);
    undo(editor);
    expect(editor.store.get().said.text).toBe("Back to was own");
    // Nothing left to undo: nothing said, nothing brought into view.
    const before = editor.store.get();
    undo(editor);
    expect(editor.store.get().said).toBe(before.said);
    expect(editor.store.get().scrollTo).toBe(before.scrollTo);
  });

  test("the same words said again are heard again, and the same span brought into view again", () => {
    typed("own", "one");
    undo(editor);
    const first = editor.store.get();
    redo(editor);
    undo(editor);
    const again = editor.store.get();
    expect(again.said.text).toBe(first.said.text);
    expect(again.said.count).not.toBe(first.said.count);
    expect(again.scrollTo?.count).not.toBe(first.scrollTo?.count);
  });

  test("putting a span back from its margin note says so and sends focus there", () => {
    typed("own", "one");
    putBack(editor, "own");
    expect(editor.store.get().reading.spans.has("own")).toBe(false);
    expect(editor.store.get().said.text).toBe("Put back was own");
    expect(editor.store.get().focusTo).toEqual({ spanId: "own" });
  });

  test("the bar counts changes, and the ones that won't match: in a similar font, or once one won't, looking different", () => {
    typed("own", "now");
    typed("substituted", "now");
    expect(changedCount(editor.store.get())).toBe(2);
    expect(warnedCount(editor.store.get())).toEqual({ count: 1, label: "in a similar font" });
    const turned = aSpan({ id: "turned", fidelity: "approximate", why: { code: "turned_text", params: {} } });
    // Its font's own copy draws the font's first span, but not this one: the span is what's counted.
    const elsewhere = aSpan({
      id: "elsewhere",
      font: "Kept",
      fidelity: "substitute",
      substitute: "Liberation Serif Regular",
    });
    editor = createEditor(FILE, aDoc({ spans: [...DOC.spans, turned, elsewhere], fonts: DOC.fonts }), 2);
    typed("substituted", "now");
    typed("turned", "now");
    typed("elsewhere", "now");
    expect(warnedCount(editor.store.get())).toEqual({ count: 3, label: "will look different" });
  });

  test("typing into text that won't match leaves why to the note as typing starts, not each letter's troubles", () => {
    const turned = aSpan({ id: "turned", fidelity: "approximate", why: { code: "turned_text", params: {} } });
    const glyphs = Object.fromEntries([..."was turned"].map((letter) => [letter, 500]));
    editor = createEditor(FILE, aDoc({ spans: [turned], fonts: [aFont("Times-Roman", { glyphs })] }), 2);
    edit(editor, "turned", null);
    expect(troublesIn(editor.store.get(), "was turned").said).toEqual([]);
  });

  test("a font quieted warns no more as typing starts, and is said; warned again, it's back", () => {
    quietFont(editor, "Named", { quiet: true });
    expect(editor.store.get().quietFonts).toEqual(new Set(["Named"]));
    expect(editor.store.get().said.text).toBe("Edits in Named won't warn again");
    quietFont(editor, "Named", { quiet: false });
    expect(editor.store.get().quietFonts).toEqual(new Set());
    expect(editor.store.get().said.text).toBe("Edits in Named warn again");
  });
});

describe("before any change", () => {
  test("the places an edit won't match are counted for the bar: in a similar font, or unlike the text around them", () => {
    expect(layoutOf(DOC).unlikeAtRest).toEqual({ count: 1, approximate: false });
    const turned = aSpan({
      id: "turned",
      font: "Kept",
      fidelity: "approximate",
      why: { code: "turned_text", params: {} },
    });
    expect(layoutOf({ ...DOC, spans: [...DOC.spans, turned] }).unlikeAtRest).toEqual({ count: 2, approximate: true });
    // Worked out once per document: the bar reads it on every change.
    const before = editor.store.get().layout.unlikeAtRest;
    typed("own", "now");
    expect(editor.store.get().layout.unlikeAtRest).toBe(before);
  });
});

describe("export", () => {
  let names: string[];

  beforeEach(() => {
    names = [];
    // Each download is a link clicked: its name is what the browser saves the file as.
    const link = {
      click: vi.fn(function (this: { download: string }) {
        names.push(this.download);
      }),
      download: "",
    };
    vi.stubGlobal("document", { createElement: () => link });
    vi.stubGlobal("URL", { createObjectURL: () => "blob:", revokeObjectURL: vi.fn() });
  });

  test("an edit still being typed goes in first", async () => {
    vi.mocked(exportPdf).mockResolvedValue({ pdf: new Blob(), skipped: [], notices: [] });
    edit(editor, "own", null);
    typeInto(editor, "typed");
    await exportNow(editor);
    expect(exportPdf).toHaveBeenCalledWith("doc", [{ kind: "replace", span_id: "own", text: "typed" }], {
      pages: undefined,
    });
    expect(editor.store.get().notices.export).toEqual([plain("Downloaded contract.pdf.")]);
    expect(editor.store.get().exporting).toBe(false);
  });

  test("edits are unexported from the first one until an export takes them, and again after an undo", async () => {
    vi.mocked(exportPdf).mockResolvedValue({ pdf: new Blob(), skipped: [], notices: [] });
    expect(unexported(editor.store.get())).toBe(false);
    typed("own", "now");
    expect(unexported(editor.store.get())).toBe(true);
    await exportNow(editor);
    expect(unexported(editor.store.get())).toBe(false);
    undo(editor);
    expect(unexported(editor.store.get())).toBe(true);
  });

  test("words still being typed are unexported too, but a field opened and left as it was isn't", () => {
    edit(editor, "own", null);
    expect(unexported(editor.store.get())).toBe(false);
    typeInto(editor, "now");
    expect(unexported(editor.store.get())).toBe(true);
  });

  test("an export that failed leaves its edits unexported", async () => {
    vi.mocked(exportPdf).mockRejectedValue(new ProblemError(aProblem(503, "Try again.")));
    typed("own", "now");
    await exportNow(editor);
    expect(unexported(editor.store.get())).toBe(true);
  });

  test("edits the server left out are said, as a warning, and so is everything else saving did otherwise", async () => {
    const tags = "Splitting the file dropped its tags, so a screen reader reads it with less structure.";
    const signed = "Every signature in this file was removed, since an edit breaks it.";
    const notices = [aFileNotice(tags), aFileNotice(signed)];
    vi.mocked(exportPdf).mockResolvedValue({ pdf: new Blob(), skipped: [0], notices });
    typed("own", "now");
    await exportNow(editor);
    expect(editor.store.get().notices.export).toEqual([warn(COPY.export_left_out), warn(tags), warn(signed)]);
    expect(editor.store.get().said.text).toBe(`${COPY.export_left_out} ${tags} ${signed}`);
  });

  test("a bug in export is said and logged, not thrown, and export can be tried again", async () => {
    const logged = vi.spyOn(console, "error").mockImplementation(() => undefined);
    vi.mocked(exportPdf).mockRejectedValue(new TypeError("undefined is not a function"));
    await exportNow(editor);
    expect(editor.store.get().notices.export.map((notice) => notice.tone)).toEqual(["warn"]);
    expect(logged).toHaveBeenCalledWith(new TypeError("undefined is not a function"));
    expect(editor.store.get().exporting).toBe(false);
  });

  test("a failure is said in the server's words, and export can be tried again", async () => {
    const problem = aProblem(422, "Couldn't remove it, so nothing was downloaded.");
    vi.mocked(exportPdf).mockRejectedValue(new ProblemError(problem));
    await exportNow(editor);
    expect(editor.store.get().notices.export).toEqual([warn(problem.detail)]);
    expect(editor.store.get().exporting).toBe(false);
  });

  test("a split downloads two files, every edit in each, named for the pages each holds", async () => {
    vi.mocked(exportPdf).mockResolvedValue({ pdf: new Blob(), skipped: [], notices: [] });
    const threePages = createEditor(FILE, { ...DOC, pages: [A4, A4, A4] }, 2);
    edit(threePages, "own", null);
    typeInto(threePages, "now");
    finish(threePages, true);
    await splitAfter(threePages, 0);
    const edits = [{ kind: "replace", span_id: "own", text: "now" }];
    expect(vi.mocked(exportPdf).mock.calls).toEqual([
      ["doc", edits, { pages: [0] }],
      ["doc", edits, { pages: [1, 2] }],
    ]);
    expect(names).toEqual(["contract, page 1.pdf", "contract, pages 2–3.pdf"]);
    expect(threePages.store.get().notices.export).toEqual([
      plain("Downloaded contract, page 1.pdf."),
      plain("Downloaded contract, pages 2–3.pdf."),
    ]);
    expect(unexported(threePages.store.get())).toBe(false);
  });

  test("a split says once what both files say, and stops at the first file that fails", async () => {
    const tags = "The tags that tell screen readers how to read this file went with the pages left out.";
    vi.mocked(exportPdf).mockResolvedValue({ pdf: new Blob(), skipped: [], notices: [aFileNotice(tags)] });
    const twoPages = createEditor(FILE, { ...DOC, pages: [A4, A4] }, 2);
    await splitAfter(twoPages, 0);
    expect(twoPages.store.get().notices.export).toEqual([warn(tags)]);

    vi.mocked(exportPdf)
      .mockReset()
      .mockRejectedValue(new ProblemError(aProblem(503, "Try again.")));
    await splitAfter(twoPages, 0);
    expect(exportPdf).toHaveBeenCalledTimes(1);
    expect(twoPages.store.get().notices.export).toEqual([warn("Try again.")]);
  });
});

describe("a split's parts", () => {
  test("are the pages up to the split, and the rest", () => {
    expect(splitPages(4, 1)).toEqual([
      [0, 1],
      [2, 3],
    ]);
  });

  test("are named from the upload, with the pages they hold counted from one", () => {
    expect(partName("contract.pdf", [0, 1, 2])).toBe("contract, pages 1–3.pdf");
    expect(partName("Contract.PDF", [3])).toBe("Contract, page 4.pdf");
  });
});
