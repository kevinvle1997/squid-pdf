import { beforeEach, describe, expect, test, vi } from "vitest";
import { ProblemError, renderStrips } from "../api/client";
import type { Render } from "../api/types";
import {
  aDoc,
  aDocumentNotice,
  aFit,
  aFont,
  aProblem,
  aReply,
  aSpan,
  halfWideGlyphs,
  PSEUDO_SENTENCES,
} from "../fixtures";
import {
  atRestSentence,
  changedCount,
  changesShown,
  createEditor,
  type Editor,
  layoutOf,
  putBack,
  redo,
  runLongSentence,
  undo,
  warnedCount,
} from "./editor";
import type { Mismatches } from "./mismatches";
import { plain, warn } from "./notices";
import { edit, finish, typeInto } from "./typing";

vi.mock(import("../api/client"), async (original) => ({ ...(await original()), renderStrips: vi.fn() }));

// "Kept" is stored in the file and draws every letter typed here; "Named" is only named, so a similar font draws it.
// Its sentences are the server's, in a language made up for tests, so any the browser writes itself stands out.
const DOC = aDoc({
  sentences: PSEUDO_SENTENCES,
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

    expect(editor.store.get().announcement.text).toBe("CHANGED TO “then”.");
  });

  test("undo and redo say what the span reads now and bring it into view, and focus stays where it is", () => {
    typed("own", "one");
    typed("own", "two");
    undo(editor);
    expect(editor.store.get().reading.spans.get("own")?.text).toBe("one");
    expect(editor.store.get().announcement.text).toBe("BACK TO “one”.");
    expect(editor.store.get().scrollTo?.spanId).toBe("own");
    expect(editor.store.get().focusTo).toBeNull();
    redo(editor);
    expect(editor.store.get().announcement.text).toBe("CHANGED TO “two”.");
    undo(editor);
    undo(editor);
    expect(editor.store.get().announcement.text).toBe("BACK TO “was own”.");
    // Nothing left to undo: nothing announced, nothing brought into view.
    const before = editor.store.get();
    undo(editor);
    expect(editor.store.get().announcement).toBe(before.announcement);
    expect(editor.store.get().scrollTo).toBe(before.scrollTo);
  });

  test("a reading persisted before undo's sentence was sent still undoes, and says nothing until the server's lands", () => {
    const stale = structuredClone(DOC);
    Reflect.deleteProperty(stale.sentences, "back_to");
    editor = createEditor(FILE, stale, 2);
    typed("own", "one");
    undo(editor);
    expect(editor.store.get().reading.spans.has("own")).toBe(false);
    expect(editor.store.get().announcement.text).toBe("");
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
    expect(editor.store.get().announcement.text).toBe("PUT BACK “was own”.");
    expect(editor.store.get().focusTo).toEqual({ spanId: "own" });
  });

  test("the bar counts changes, and the ones that won't match: in a similar font, or once one won't, looking different", () => {
    typed("own", "now");
    typed("substituted", "now");
    expect(changedCount(editor.store.get())).toBe(2);
    expect(warnedCount(editor.store.get())).toEqual({ count: 1, different: false });
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
    expect(warnedCount(editor.store.get())).toEqual({ count: 3, different: true });
  });
});

describe("the bar's count", () => {
  test("says the server's sentences, whole, the part that warns kept apart as Find's is", () => {
    const shown = (changed: number, count: number, different: boolean) =>
      changesShown({ changed, count, different }, PSEUDO_SENTENCES);
    expect(shown(3, 0, false)).toEqual({ plain: "3 CHANGES", warned: "" });
    expect(shown(1, 1, false)).toEqual({ plain: "", warned: "1 CHANGE IN A SIMILAR FONT" });
    expect(shown(2, 2, true)).toEqual({ plain: "", warned: "2 CHANGES WILL LOOK DIFFERENT" });
    expect(shown(3, 1, false)).toEqual({ plain: "3 CHANGES, ", warned: "1 OF THEM IN A SIMILAR FONT" });
    expect(shown(1234, 2, true)).toEqual({ plain: "1,234 CHANGES, ", warned: "2 OF THEM WILL LOOK DIFFERENT" });
  });

  test("before any change, says the places an edit won't match in the server's sentence, nothing when there are none", () => {
    const atRest = (count: number, reasons: Mismatches["reasons"]) =>
      atRestSentence({ count, fonts: [], reasons }, PSEUDO_SENTENCES);
    expect(atRest(0, [])).toBe("");
    expect(atRest(1, [])).toBe("EDITS IN 1 PLACE WILL USE A SIMILAR FONT");
    const turned = { key: "turned_text", code: "turned_text" as const, places: 2, sentence: "" };
    expect(atRest(2, [turned])).toBe("EDITS IN 2 PLACES WILL LOOK DIFFERENT");
  });

  test("on a reading persisted before its sentences were sent, shows nothing, and never half of itself", () => {
    const stale = structuredClone(PSEUDO_SENTENCES);
    Reflect.deleteProperty(stale, "changes_some_similar");
    Reflect.deleteProperty(stale, "at_rest_similar");
    expect(changesShown({ changed: 3, count: 1, different: false }, stale)).toEqual({ plain: "", warned: "" });
    expect(atRestSentence({ count: 1, fonts: [], reasons: [] }, stale)).toBe("");
  });

  test("counts the changes left long past their room in the server's sentence, nothing where a reading lacks it", async () => {
    const tooLong = aFit({
      message: "about 12% too long",
      message_parts: [{ code: "too_long", params: { percent: 12 } }],
    });
    vi.mocked(renderStrips).mockResolvedValue(aReply({ fits: { own: tooLong } }));
    const before = editor.store.get().drawn;
    typed("own", "now and then");
    await vi.waitFor(() => expect(editor.store.get().drawn).not.toBe(before));
    expect(runLongSentence(editor.store.get())).toBe("1 TOO LONG FOR ITS PLACE");

    const stale = structuredClone(PSEUDO_SENTENCES);
    Reflect.deleteProperty(stale, "changes_too_long");
    expect(runLongSentence({ ...editor.store.get(), doc: { ...DOC, sentences: stale } })).toBe("");
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
