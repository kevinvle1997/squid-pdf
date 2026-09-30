import { beforeEach, describe, expect, test, vi } from "vitest";
import { ProblemError, exportPdf, render } from "../api/client";
import type { Document, FontInfo, ProblemInfo, Render, SpanInfo } from "../api/types";
import { type Editor, change, changedCount, createEditor, putBack, similarCount } from "./editor";
import { exportNow } from "./export";
import { edit, finish, type } from "./typing";

vi.mock(import("../api/client"), async (original) => ({
  ...(await original()),
  render: vi.fn(),
  exportPdf: vi.fn(),
  upload: vi.fn(),
  stillThere: vi.fn(),
}));

const spanOf = (id: string, font: string): SpanInfo => ({
  id,
  page: 0,
  text: `was ${id}`,
  font,
  size: 10,
  color: [0, 0, 0],
  bbox: { x0: 72, y0: 100, x1: 200, y1: 112 },
  origin: [72, 110],
  fidelity: "exact",
});
const fontOf = (name: string, substitute: string | null): FontInfo =>
  ({ name, substitute, why: null, same_widths: true, glyphs: {} }) as unknown as FontInfo;
const DOC = {
  id: "doc",
  pages: [{ width: 595, height: 842, rotation: 0 }],
  spans: [spanOf("own", "Kept"), spanOf("similar", "Named")],
  fonts: [fontOf("Kept", null), fontOf("Named", "Liberation Serif Regular")],
  notices: [],
  copy: { reopened: "Opened again.", export_left_out: "Some edits were left out." },
} as unknown as Document;
const FILE = new File(["%PDF-"], "contract.pdf");

let editor: Editor;

beforeEach(() => {
  vi.mocked(render).mockReset().mockReturnValue(new Promise<Render>(() => undefined));
  vi.mocked(exportPdf).mockReset();
  editor = createEditor(FILE, DOC, 2);
});

const typed = (spanId: string, text: string) => {
  edit(editor, spanId, null);
  type(editor, text);
  finish(editor, true);
};

describe("an edit", () => {
  test("typing holds a draft apart from the history, until it's finished", () => {
    edit(editor, "own", null);
    expect(editor.store.get().draft).toMatchObject({ spanId: "own", page: 0, text: "was own" });
    type(editor, "now");
    expect(editor.store.get().history.done).toEqual([]);
    finish(editor, true);
    expect(editor.store.get().draft).toBeNull();
    expect(editor.store.get().view.spans.get("own")?.text).toBe("now");
    expect(editor.store.get().said).toBe("Changed to now");
  });

  test("Escape, the same words, or nothing at all put nothing in the history", () => {
    edit(editor, "own", null);
    type(editor, "now");
    finish(editor, false);
    typed("own", "was own");
    typed("own", "   ");
    expect(editor.store.get().history.done).toEqual([]);
  });

  test("a change goes to the server to draw, and clears a plain message, not a warning", () => {
    editor.store.set({ notice: { tone: "plain", text: "Downloaded contract.pdf." } });
    typed("own", "now");
    expect(editor.store.get().notice).toBeNull();
    expect(render).toHaveBeenCalledTimes(1);

    editor.store.set({ notice: { tone: "warn", text: "Couldn't reach the server." } });
    typed("own", "later");
    expect(editor.store.get().notice?.tone).toBe("warn");
  });

  test("undo, redo and putting a span back from its margin note", () => {
    typed("own", "one");
    typed("own", "two");
    change(editor, { kind: "undo" });
    expect(editor.store.get().view.spans.get("own")?.text).toBe("one");
    change(editor, { kind: "redo" });
    putBack(editor, "own");
    expect(editor.store.get().view.spans.has("own")).toBe(false);
    expect(editor.store.get().said).toBe("Put back was own");
  });

  test("the bar counts changes, and the ones drawn in a similar font", () => {
    typed("own", "now");
    typed("similar", "now");
    const state = editor.store.get();
    expect(changedCount(state)).toBe(2);
    expect(similarCount(state)).toBe(1);
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
    type(editor, "typed");
    await exportNow(editor);
    expect(exportPdf).toHaveBeenCalledWith("doc", [{ kind: "replace", span_id: "own", text: "typed" }]);
    expect(editor.store.get().notice).toEqual({ tone: "plain", text: "Downloaded contract.pdf." });
    expect(editor.store.get().exporting).toBe(false);
  });

  test("edits the server left out are said, as a warning", async () => {
    vi.mocked(exportPdf).mockResolvedValue({ pdf: new Blob(), skipped: [0], notices: [] });
    typed("own", "now");
    await exportNow(editor);
    expect(editor.store.get().notice).toEqual({ tone: "warn", text: "Some edits were left out." });
  });

  test("a failure is said in the server's words, and export can be tried again", async () => {
    const problem = { status: 422, detail: "Couldn't remove it, so nothing was downloaded." } as ProblemInfo;
    vi.mocked(exportPdf).mockRejectedValue(new ProblemError(problem));
    await exportNow(editor);
    expect(editor.store.get().notice).toEqual({ tone: "warn", text: problem.detail });
    expect(editor.store.get().exporting).toBe(false);
  });
});
