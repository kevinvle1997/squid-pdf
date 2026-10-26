import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { renderStrips } from "../api/client";
import type { FitInfo, Render } from "../api/types";
import { aDoc, aFit, aFont, aRedact, aReplace, aSpan, halfWideGlyphs } from "../fixtures";
import { FIND_DELAY_MS } from "./constants";
import { change, createEditor, type Editor, type EditorState, undo } from "./editor";
import {
  changeAll,
  closeFind,
  countSentence,
  countShown,
  drawnWidthOf,
  findCount,
  matchesAcross,
  openFind,
  placeAt,
  setMatchCase,
  setWholeWords,
  stepFind,
  tintsOn,
  typeQuery,
  typeReplacement,
} from "./find";
import type { LineScale } from "./fit";
import { matchesIn } from "./matching";
import { edit, typeInto } from "./typing";

vi.mock(import("../api/client"), async (original) => ({ ...(await original()), renderStrips: vi.fn() }));

// Every letter these tests type, as wide as each other, so "14 June" fits where "14 March" was.
const glyphs = halfWideGlyphs(" 0123456789MarchJunewsoifdtly");
// A line set as the span is: no way out taken.
const AS_SET: LineScale = { size: 1, scaleX: 1 };
const DATE = "14 March";
const SPANS = [
  aSpan({ id: "first", text: `was ${DATE}`, room_pt: 50 }),
  aSpan({ id: "similar", text: `${DATE} too`, font: "Named", fidelity: "substitute", substitute: "Liberation Serif" }),
  aSpan({ id: "field", text: `${DATE} filled`, form_field: true }),
  aSpan({ id: "redacted", text: `${DATE} secret`, page: 1 }),
  aSpan({ id: "other", text: "nothing to find", page: 1 }),
];
const DOC = aDoc({ spans: SPANS, fonts: [aFont("Times-Roman", { glyphs }), aFont("Named", { glyphs })] });

let editor: Editor;

beforeEach(() => {
  vi.useFakeTimers();
  vi.mocked(renderStrips).mockReturnValue(new Promise<Render>(() => undefined));
  editor = createEditor(new File(["%PDF-"], "contract.pdf"), DOC, 2);
  change(editor, { kind: "add", edits: [aRedact("redacted")] });
  openFind(editor);
});

afterEach(() => {
  vi.useRealTimers();
});

const found = () => [...editor.store.get().find.matches].map(([page, spans]) => [page, [...spans.keys()]]);

/** Each state `run` sets, as what's drawn would hear of it. */
function updatesOf(run: () => void): EditorState[] {
  const heard: EditorState[] = [];
  const stop = editor.store.subscribe(() => heard.push(editor.store.get()));
  run();
  stop();
  return heard;
}

describe("find", () => {
  test("finds once typing stops, in every span that reads it but a form field's or a redaction's", () => {
    typeQuery(editor, DATE);
    expect(editor.store.get().find.query).toBe(DATE);
    expect(found()).toEqual([]);

    vi.advanceTimersByTime(FIND_DELAY_MS);
    expect(found()).toEqual([[0, ["first", "similar"]]]);
    expect(editor.store.get().announcement.text).toBe("2 places, 1 of them will look different");
  });

  test("counts a place as looking different for its font, and for the words it would read", () => {
    typeQuery(editor, DATE);
    vi.advanceTimersByTime(FIND_DELAY_MS);
    expect(findCount(editor.store.get())).toEqual({ places: 2, mismatches: 1 });

    typeReplacement(editor, "14 June");
    expect(findCount(editor.store.get())).toEqual({ places: 2, mismatches: 1 });
    // A letter the font hasn't got, and a line that runs past its room.
    typeReplacement(editor, "14 Juné");
    expect(findCount(editor.store.get())).toEqual({ places: 2, mismatches: 2 });
    typeReplacement(editor, "14 June and the whole of the year that follows it");
    expect(findCount(editor.store.get())).toEqual({ places: 2, mismatches: 2 });
  });

  test("says the count in whole words", () => {
    expect(countSentence({ places: 0, mismatches: 0 })).toBe("Nothing matches");
    expect(countSentence({ places: 1, mismatches: 0 })).toBe("1 place will look identical");
    expect(countSentence({ places: 14, mismatches: 3 })).toBe("14 places, 3 of them will look different");
    expect(countSentence({ places: 2, mismatches: 2 })).toBe("2 places will look different");
  });

  test("keeps apart the part of the count that warns, for --warn, and nothing else", () => {
    expect(countShown({ places: 14, mismatches: 3 })).toEqual({
      plain: "14 places, ",
      warned: "3 of them will look different",
    });
    expect(countShown({ places: 2, mismatches: 2 })).toEqual({ plain: "", warned: "2 places will look different" });
    expect(countShown({ places: 1, mismatches: 0 })).toEqual({ plain: "1 place will look identical", warned: "" });
  });

  test("Change all is one step: every place changes together, and one undo puts them all back", () => {
    typeQuery(editor, DATE);
    typeReplacement(editor, "14 June");
    // Pressed before the find has run: it changes what "Find" shows.
    changeAll(editor);
    const { reading, find, announcement } = editor.store.get();
    expect(reading.spans.get("first")?.text).toBe("was 14 June");
    expect(reading.spans.get("similar")?.text).toBe("14 June too");
    expect(find.changed).toBe(2);
    expect(found()).toEqual([]);
    expect(announcement.text).toBe("Changed 2 places.");

    undo(editor);
    expect(editor.store.get().reading.spans.get("first")).toBeUndefined();
    expect(editor.store.get().reading.spans.get("similar")).toBeUndefined();
  });

  test("Change all while typing keeps the words, as their own step, found as typed, all in one update", () => {
    edit(editor, "other", null);
    typeInto(editor, `${DATE} now`);
    typeQuery(editor, DATE);
    typeReplacement(editor, "14 June");
    const [changed, ...more] = updatesOf(() => changeAll(editor));
    expect(more).toEqual([]);
    expect(changed?.draft).toBeNull();
    expect(changed?.reading.spans.get("other")?.text).toBe("14 June now");
    expect(changed?.find.changed).toBe(3);

    undo(editor);
    expect(editor.store.get().reading.spans.get("other")?.text).toBe(`${DATE} now`);
  });

  test("Find opened while typing keeps the words and opens in the same update", () => {
    closeFind(editor);
    edit(editor, "other", null);
    typeInto(editor, "now");
    const [opened, ...more] = updatesOf(() => openFind(editor));
    expect(more).toEqual([]);
    expect(opened?.draft).toBeNull();
    expect(opened?.reading.spans.get("other")?.text).toBe("now");
    expect(opened?.find.open).toBe(true);
  });

  test("what Change all did stops being shown once anything else changes: undo would take that, not the places", () => {
    typeQuery(editor, DATE);
    typeReplacement(editor, "14 June");
    changeAll(editor);
    expect(editor.store.get().find.changed).toBe(2);
    change(editor, { kind: "add", edits: [aRedact("other")] });
    expect(editor.store.get().find.changed).toBeNull();

    // Closed meanwhile, the bar doesn't say it again when it opens.
    changeAll(editor);
    closeFind(editor);
    undo(editor);
    openFind(editor);
    expect(editor.store.get().find.changed).toBeNull();
  });

  test("Change all needs words to change to: emptying a span is redaction's job", () => {
    typeQuery(editor, DATE);
    vi.advanceTimersByTime(FIND_DELAY_MS);
    const before = editor.store.get().history;
    changeAll(editor);
    expect(editor.store.get().history).toBe(before);
  });

  test("an edit elsewhere keeps a page's marks the same object, so that page doesn't redraw", () => {
    typeQuery(editor, DATE);
    vi.advanceTimersByTime(FIND_DELAY_MS);
    const marks = editor.store.get().find.matches.get(0);
    change(editor, { kind: "add", edits: [aReplace("other", "still nothing")] });
    expect(editor.store.get().find.matches.get(0)).toBe(marks);
  });

  test("closing clears the marks and stops a find still waiting to run", () => {
    typeQuery(editor, DATE);
    closeFind(editor, { fromInside: true });
    vi.advanceTimersByTime(FIND_DELAY_MS);
    const { find } = editor.store.get();
    expect(find.open).toBe(false);
    expect(find.matches.size).toBe(0);
    expect(find.closed).toBe(1);
  });

  test("Match case and Whole words find again at once and say the count", () => {
    typeQuery(editor, "14 MARCH");
    vi.advanceTimersByTime(FIND_DELAY_MS);
    expect(found()).toEqual([[0, ["first", "similar"]]]);
    setMatchCase(editor, true);
    expect(editor.store.get().find.matchCase).toBe(true);
    expect(found()).toEqual([]);
    expect(editor.store.get().announcement.text).toBe("Nothing matches");
    setMatchCase(editor, false);
    setWholeWords(editor, true);
    typeQuery(editor, "14 Mar");
    vi.advanceTimersByTime(FIND_DELAY_MS);
    expect(found()).toEqual([]);
  });

  test("a match sits across its span by its letters' widths, so only the words found are marked", () => {
    // Ten 5 pt letters across 50 pt: "cdef" is the third to the sixth.
    const line = aSpan({ id: "line", text: "abcdefghij", size: 10, bbox: { x0: 100, y0: 0, x1: 150, y1: 12 } });
    const glyphs = halfWideGlyphs("abcdefghij");
    const matches = matchesIn(line.text, "cdef", { matchCase: false, wholeWords: false });
    expect(matchesAcross(line, { text: line.text, matches, glyphs, widePt: 50 })).toEqual([{ x0: 110, x1: 130 }]);
  });

  describe("in an edited span, a match sits where the edit draws its words, not where the original's were", () => {
    // Ten 5 pt letters across 50 pt, edited to other words of the same letters.
    const line = aSpan({ id: "line", text: "abcdefghij", size: 10, bbox: { x0: 100, y0: 0, x1: 150, y1: 12 } });
    const glyphs = halfWideGlyphs("abcdefghijklmnopqrst");
    const across = (text: string, find: string, drawn: { scale?: LineScale; fit?: FitInfo } = {}) => {
      const { scale = AS_SET, fit } = drawn;
      const widePt = drawnWidthOf(line, { text, glyphs, scale, fit });
      const matches = matchesIn(text, find, { matchCase: false, wholeWords: false });
      return matchesAcross(line, { text, matches, glyphs, widePt });
    };

    test("a shorter edit: the words found are as far in as their letters", () => {
      expect(across("abcde", "cd")).toEqual([{ x0: 110, x1: 120 }]);
    });

    test("a longer edit left long: past where the original ended", () => {
      expect(across("abcdefghijklmnopqrst", "klmn")).toEqual([{ x0: 150, x1: 170 }]);
    });

    test("a longer edit narrowed to end where the original did: narrowed with it", () => {
      expect(across("abcdefghijklmnopqrst", "klmn", { scale: { size: 1, scaleX: 0.5 } })).toEqual([
        { x0: 125, x1: 135 },
      ]);
    });

    test("once the server drew it, as long as its fit says: a similar font may draw it wider than the file's widths", () => {
      const fit = aFit({ strategy: "as-is", delta_pt: 70 });
      expect(across("abcdefghijklmnopqrst", "klmn", { fit })).toEqual([{ x0: 160, x1: 184 }]);
    });

    test("once the server drew it with a way out, ending where the original did, whatever the browser would have", () => {
      const fit = aFit({ strategy: "condense", delta_pt: 50 });
      expect(across("abcdefghijklmnopqrst", "klmn", { fit })).toEqual([{ x0: 125, x1: 135 }]);
    });

    test("a page takes the server's fit only while its strips show these words: the browser's preview, until then", () => {
      const reading = {
        span: line,
        text: "abcdefghijklmnopqrst",
        replaced: true,
        strategy: undefined,
        redacted: false,
      };
      const edits = { spans: [reading], inserts: [] };
      const page = {
        spans: [line],
        edits,
        fits: { [line.id]: aFit({ delta_pt: 70 }) },
        fonts: new Map([[line.font, aFont(line.font, { glyphs })]]),
        rules: aDoc().fit,
        typingIn: null,
      };
      const matches = new Map([[line.id, matchesIn(reading.text, "klmn", { matchCase: false, wholeWords: false })]]);
      const tints = (drawnFrom: typeof edits | undefined) =>
        tintsOn({ ...page, drawnFrom }, matches).map(({ x0, x1 }) => ({ x0, x1 }));
      expect(tints(edits)).toEqual([{ x0: 160, x1: 184 }]);
      expect(tints(undefined)).toEqual([{ x0: 150, x1: 170 }]);
    });

    test("without the font's widths, by letter count across its box", () => {
      const widePt = drawnWidthOf(line, { text: "ab cd", glyphs: undefined, scale: AS_SET, fit: undefined });
      const matches = matchesIn("ab cd", "cd", { matchCase: false, wholeWords: false });
      expect(matchesAcross(line, { text: "ab cd", matches, glyphs: undefined, widePt })).toEqual([
        { x0: 130, x1: 150 },
      ]);
    });
  });
});

describe("stepping through the places found", () => {
  const current = () => editor.store.get().find.current;

  test("Enter goes to each place in the document's order, round from the last, brought into the middle and announced", () => {
    typeQuery(editor, DATE);
    vi.advanceTimersByTime(FIND_DELAY_MS);
    stepFind(editor, 1);
    expect(current()).toBe("first");
    expect(editor.store.get().announcement.text).toBe("1 of 2");
    expect(editor.store.get().scrollTo).toMatchObject({ spanId: "first", block: "center" });
    stepFind(editor, 1);
    expect(current()).toBe("similar");
    stepFind(editor, 1);
    expect(current()).toBe("first");
    stepFind(editor, -1);
    expect(current()).toBe("similar");
    expect(placeAt(editor.store.get().find)).toEqual({ at: 2, of: 2 });
  });

  test("a step before typing has stopped finds first, and a new search starts from the top again", () => {
    typeQuery(editor, DATE);
    stepFind(editor, -1);
    expect(current()).toBe("similar");
    // The search typing would have run is done already: it doesn't come back to undo the step.
    vi.advanceTimersByTime(FIND_DELAY_MS);
    expect(current()).toBe("similar");
    typeQuery(editor, "14 M");
    vi.advanceTimersByTime(FIND_DELAY_MS);
    expect(current()).toBeNull();
    expect(placeAt(editor.store.get().find)).toBeNull();
  });

  test("with find shut, nothing is gone to", () => {
    closeFind(editor);
    stepFind(editor, 1);
    expect(current()).toBeNull();
  });
});
