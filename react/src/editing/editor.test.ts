import { beforeEach, describe, expect, test, vi } from "vitest";
import { ProblemError, renderStrips } from "../api/client";
import type { Render } from "../api/types";
import { aDoc, aDocumentNotice, aFont, aProblem, aSpan, halfWideGlyphs } from "../fixtures";
import { changedCount, createEditor, type Editor, layoutOf, putBack, redo, undo, warnedCount } from "./editor";
import { plain, warn } from "./notices";
import { edit, finish, typeInto } from "./typing";

vi.mock(import("../api/client"), async (original) => ({ ...(await original()), renderStrips: vi.fn() }));

// "Kept" is stored in the file and draws every letter typed here; "Named" is only named, so a similar font draws it.
const DOC = aDoc({
  spans: [
    aSpan({ id: "own", font: "Kept" }),
    aSpan({
      id: "substituted",
      font: "Named",
      fidelity: "substitute",
      substitute: "Liberation Serif Regular",
      in_file: false,
    }),
  ],
  fonts: [
    aFont("Kept", { glyphs: halfWideGlyphs("abcdefghijklmnopqrstuvwxyz ") }),
    aFont("Named", { substitute: "Liberation Serif Regular" }),
  ],
});
const FILE = new File(["%PDF-"], "contract.pdf");

let editor: Editor;

beforeEach(() => {
  vi.mocked(renderStrips)
    .mockReset()
    .mockReturnValue(new Promise<Render>(() => undefined));
  editor = createEditor(FILE, DOC, 2);
});

const typed = (spanId: string, text: string) => {
  edit(editor, spanId, null);
  typeInto(editor, text);
  finish(editor, true);
};

describe("a change to the history", () => {
  test("a change goes to the server to draw; what export, reopening and a drop said goes, the document's stays", () => {
    const found = aDocumentNotice("This file is signed.", "signed");
    editor = createEditor(FILE, { ...DOC, notices: [found] }, 2);
    const { notices } = editor.store.get();
    const drop = warn("Only a PDF can be dropped here.");
    editor.store.set({
      notices: { ...notices, export: [plain("Downloaded contract.pdf.")], reopen: plain("Opened."), drop },
    });
    typed("own", "now");
    expect(editor.store.get().notices).toEqual({
      document: [{ ...warn(found.detail), type: "signed" }],
      export: [],
      reopen: null,
      font: null,
      drop: null,
    });
    expect(renderStrips).toHaveBeenCalledTimes(1);
  });

  test("a render that fails is heard as well as shown, and the same failure again isn't announced twice", async () => {
    const down = "We can't work on files right now. Try again in a few minutes.";
    vi.mocked(renderStrips).mockRejectedValue(new ProblemError(aProblem(503, down)));
    typed("own", "now");
    await vi.waitFor(() => expect(editor.store.get().announcement.text).toBe(down));

    typed("own", "then");
    const failedOnce = editor.store.get().drawn;
    await vi.waitFor(() => expect(editor.store.get().drawn).not.toBe(failedOnce));

    expect(editor.store.get().announcement.text).toBe("Changed to then");
  });

  test("undo and redo say what the span reads now and bring it into view, and focus stays where it is", () => {
    typed("own", "one");
    typed("own", "two");
    undo(editor);
    expect(editor.store.get().reading.spans.get("own")?.text).toBe("one");
    expect(editor.store.get().announcement.text).toBe("Back to one");
    expect(editor.store.get().scrollTo?.spanId).toBe("own");
    expect(editor.store.get().focusTo).toBeNull();
    redo(editor);
    expect(editor.store.get().announcement.text).toBe("Changed to two");
    undo(editor);
    undo(editor);
    expect(editor.store.get().announcement.text).toBe("Back to was own");
    // Nothing left to undo: nothing announced, nothing brought into view.
    const before = editor.store.get();
    undo(editor);
    expect(editor.store.get().announcement).toBe(before.announcement);
    expect(editor.store.get().scrollTo).toBe(before.scrollTo);
  });

  test("the same words announced again are heard again, and the same span brought into view again", () => {
    typed("own", "one");
    undo(editor);
    const first = editor.store.get();
    redo(editor);
    undo(editor);
    const again = editor.store.get();
    expect(again.announcement.text).toBe(first.announcement.text);
    expect(again.announcement.count).not.toBe(first.announcement.count);
    expect(again.scrollTo?.count).not.toBe(first.scrollTo?.count);
  });

  test("putting a span back from its margin note says so and sends focus there", () => {
    typed("own", "one");
    putBack(editor, "own");
    expect(editor.store.get().reading.spans.has("own")).toBe(false);
    expect(editor.store.get().announcement.text).toBe("Put back was own");
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
      in_file: false,
    });
    editor = createEditor(FILE, aDoc({ spans: [...DOC.spans, turned, elsewhere], fonts: DOC.fonts }), 2);
    typed("substituted", "now");
    typed("turned", "now");
    typed("elsewhere", "now");
    expect(warnedCount(editor.store.get())).toEqual({ count: 3, label: "will look different" });
  });
});

describe("before any change", () => {
  test("the places an edit won't match are counted for the bar: in a similar font, or set apart from the text around them", () => {
    expect(layoutOf(DOC).mismatchesAtRest).toMatchObject({ count: 1, reasons: [] });
    const turned = aSpan({
      id: "turned",
      font: "Kept",
      fidelity: "approximate",
      why: { code: "turned_text", params: {} },
    });
    expect(layoutOf({ ...DOC, spans: [...DOC.spans, turned] }).mismatchesAtRest).toMatchObject({
      count: 2,
      reasons: [{ code: "turned_text", places: 1 }],
    });
    // Worked out once per document: the bar reads it on every change.
    const before = editor.store.get().layout.mismatchesAtRest;
    typed("own", "now");
    expect(editor.store.get().layout.mismatchesAtRest).toBe(before);
  });
});
