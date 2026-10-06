import { beforeEach, describe, expect, test, vi } from "vitest";
import { exportPdf, ProblemError, render } from "../api/client";
import type { Render } from "../api/types";
import { aDoc, aFont, aProblem, aSpan, COPY } from "../fixtures";
import { changedCount, createEditor, type Editor, putBack, redo, undo, unexported, warnedCount } from "./editor";
import { exportNow } from "./export";
import { plain, warn } from "./notices";
import { edit, enter, finish, troublesIn, typeInto } from "./typing";

vi.mock(import("../api/client"), async (original) => ({
  ...(await original()),
  render: vi.fn(),
  exportPdf: vi.fn(),
  upload: vi.fn(),
  stillThere: vi.fn(),
}));

const DOC = aDoc({
  spans: [aSpan({ id: "own", font: "Kept" }), aSpan({ id: "substituted", font: "Named" })],
  fonts: [aFont("Kept"), aFont("Named", { substitute: "Liberation Serif Regular" })],
});
const FILE = new File(["%PDF-"], "contract.pdf");

let editor: Editor;

beforeEach(() => {
  vi.mocked(render)
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

  test("a change goes to the server to draw; what export and reopening said goes, the document's stays", () => {
    const found = { code: "signed", params: {}, type: "t", detail: "This file is signed." };
    editor = createEditor(FILE, { ...DOC, notices: [found] }, 2);
    const { notices } = editor.store.get();
    editor.store.set({ notices: { ...notices, export: plain("Downloaded contract.pdf."), reopen: plain("Opened.") } });
    typed("own", "now");
    expect(editor.store.get().notices).toEqual({
      document: [warn(found.detail)],
      export: null,
      reopen: null,
      font: null,
    });
    expect(render).toHaveBeenCalledTimes(1);
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
    editor = createEditor(FILE, aDoc({ spans: [...DOC.spans, turned], fonts: DOC.fonts }), 2);
    typed("substituted", "now");
    typed("turned", "now");
    expect(warnedCount(editor.store.get())).toEqual({ count: 2, label: "will look different" });
  });

  test("typing into text that won't match says why, before any trouble of its own", () => {
    const turned = aSpan({ id: "turned", fidelity: "approximate", why: { code: "turned_text", params: {} } });
    const glyphs = Object.fromEntries([..."was turned"].map((letter) => [letter, 500]));
    editor = createEditor(FILE, aDoc({ spans: [turned], fonts: [aFont("Times-Roman", { glyphs })] }), 2);
    edit(editor, "turned", null);
    expect(troublesIn(editor.store.get(), "was turned").said).toEqual([COPY.approximate.turned_text]);
  });
});

describe("export", () => {
  beforeEach(() => {
    vi.stubGlobal("document", { createElement: () => ({ click: vi.fn() }) });
    vi.stubGlobal("URL", { createObjectURL: () => "blob:", revokeObjectURL: vi.fn() });
  });

  test("an edit still being typed goes in first", async () => {
    vi.mocked(exportPdf).mockResolvedValue({ pdf: new Blob(), skipped: [], notices: [] });
    edit(editor, "own", null);
    typeInto(editor, "typed");
    await exportNow(editor);
    expect(exportPdf).toHaveBeenCalledWith("doc", [{ kind: "replace", span_id: "own", text: "typed" }]);
    expect(editor.store.get().notices.export).toEqual(plain("Downloaded contract.pdf."));
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

  test("edits the server left out are said, as a warning", async () => {
    vi.mocked(exportPdf).mockResolvedValue({ pdf: new Blob(), skipped: [0], notices: [] });
    typed("own", "now");
    await exportNow(editor);
    expect(editor.store.get().notices.export).toEqual(warn(COPY.export_left_out));
  });

  test("a bug in export is said and logged, not thrown, and export can be tried again", async () => {
    const logged = vi.spyOn(console, "error").mockImplementation(() => undefined);
    vi.mocked(exportPdf).mockRejectedValue(new TypeError("undefined is not a function"));
    await exportNow(editor);
    expect(editor.store.get().notices.export?.tone).toBe("warn");
    expect(logged).toHaveBeenCalledWith(new TypeError("undefined is not a function"));
    expect(editor.store.get().exporting).toBe(false);
  });

  test("a failure is said in the server's words, and export can be tried again", async () => {
    const problem = aProblem(422, "Couldn't remove it, so nothing was downloaded.");
    vi.mocked(exportPdf).mockRejectedValue(new ProblemError(problem));
    await exportNow(editor);
    expect(editor.store.get().notices.export).toEqual(warn(problem.detail));
    expect(editor.store.get().exporting).toBe(false);
  });
});
