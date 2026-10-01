import { beforeEach, describe, expect, test, vi } from "vitest";
import { exportPdf, ProblemError, render } from "../api/client";
import type { Render } from "../api/types";
import { aDoc, aFont, aProblem, aSpan, COPY } from "../fixtures";
import { change, changedCount, createEditor, type Editor, putBack, substitutedCount } from "./editor";
import { exportNow } from "./export";
import { plain, warn } from "./notices";
import { edit, finish, troublesIn, typeInto } from "./typing";

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
    expect(editor.store.get().said).toBe("Changed to now");
  });

  test("a trouble is said as it appears or changes, not as its numbers tick by with each letter", () => {
    const glyphs = { a: 500, b: 500 }; // 5 pt each at 10 pt
    const doc = aDoc({ spans: [aSpan({ id: "ab", text: "ab" })], fonts: [aFont("Times-Roman", { glyphs })] });
    editor = createEditor(FILE, doc, 2);
    edit(editor, "ab", null);
    typeInto(editor, "abab");
    expect(editor.store.get().said).toBe("10.0 pt too long");
    typeInto(editor, "ababa");
    expect(editor.store.get().said).toBe("10.0 pt too long");
    typeInto(editor, "ababac");
    expect(editor.store.get().said).toContain("no c in this font");
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
    expect(editor.store.get().notices).toEqual({ document: [warn(found.detail)], export: null, reopen: null });
    expect(render).toHaveBeenCalledTimes(1);
  });

  test("undo, redo and putting a span back from its margin note", () => {
    typed("own", "one");
    typed("own", "two");
    change(editor, { kind: "undo" });
    expect(editor.store.get().reading.spans.get("own")?.text).toBe("one");
    change(editor, { kind: "redo" });
    putBack(editor, "own");
    expect(editor.store.get().reading.spans.has("own")).toBe(false);
    expect(editor.store.get().said).toBe("Put back was own");
  });

  test("the bar counts changes, and the ones drawn in a similar font", () => {
    typed("own", "now");
    typed("substituted", "now");
    const state = editor.store.get();
    expect(changedCount(state)).toBe(2);
    expect(substitutedCount(state)).toBe(1);
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
