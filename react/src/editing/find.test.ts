import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { renderStrips } from "../api/client";
import type { FitInfo, Render, Sentences } from "../api/types";
import { aDoc, aFit, aFont, aRedact, aReplace, aSpan, halfWideGlyphs, PSEUDO_SENTENCES } from "../fixtures";
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
import { NOTHING_DRAWN } from "./render";
import { edit, troublesIn, typeInto } from "./typing";
import { shortWarningOf } from "./verdicts";

vi.mock(import("../api/client"), async (original) => ({ ...(await original()), renderStrips: vi.fn() }));

// Every letter these tests type, as wide as each other, so "14 June" fits where "14 March" was.
const glyphs = halfWideGlyphs(" 0123456789MarchJunewsoifdtly");
// A line set as the span is: no way out taken.
const AS_SET: LineScale = { size: 1, scaleX: 1 };
const DATE = "14 March";
const SPANS = [
  aSpan({ id: "first", text: `was ${DATE}`, room_pt: 50 }),
  aSpan({
    id: "similar",
    text: `${DATE} too`,
    font: "Named",
    fidelity: "substitute",
    substitute: "Liberation Serif",
    in_file: false,
  }),
  aSpan({ id: "field", text: `${DATE} filled`, form_field: true }),
  aSpan({ id: "redacted", text: `${DATE} secret`, page: 1 }),
  aSpan({ id: "other", text: "nothing to find", page: 1 }),
];
// Its sentences are the server's, in a language made up for tests, so any the browser writes itself stands out.
const DOC = aDoc({
  sentences: PSEUDO_SENTENCES,
  spans: SPANS,
  fonts: [aFont("Times-Roman", { glyphs }), aFont("Named", { glyphs })],
});

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

/** The chip's short warning on an edit of `spanId` reading `text`: the span's own where none is worked out, as `EditField` shows it. */
function chipWarningOf(spanId: string, text: string): string | null {
  edit(editor, spanId, null);
  const state = editor.store.get();
  const verdict = troublesIn(state, text).verdict ?? state.layout.spans.get(spanId);
  if (verdict === undefined) throw new Error(`no span ${spanId}`);
  return shortWarningOf(verdict);
}

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
    expect(editor.store.get().announcement.text).toBe("2 PLACES, 1 OF THEM WILL LOOK DIFFERENT");
  });

  test("counts a place as looking different for its font, and for the words it would read", () => {
    typeQuery(editor, DATE);
    vi.advanceTimersByTime(FIND_DELAY_MS);
    expect(findCount(editor.store.get())).toEqual({ places: 2, mismatches: 1, leftAsTheyAre: 0 });

    typeReplacement(editor, "14 June");
    vi.advanceTimersByTime(FIND_DELAY_MS);
    expect(findCount(editor.store.get())).toEqual({ places: 2, mismatches: 1, leftAsTheyAre: 0 });
    // A letter the font hasn't got, and a line that runs past its room.
    typeReplacement(editor, "14 Juné");
    vi.advanceTimersByTime(FIND_DELAY_MS);
    expect(findCount(editor.store.get())).toEqual({ places: 2, mismatches: 2, leftAsTheyAre: 0 });
    typeReplacement(editor, "14 June and the whole of the year that follows it");
    vi.advanceTimersByTime(FIND_DELAY_MS);
    expect(findCount(editor.store.get())).toEqual({ places: 2, mismatches: 2, leftAsTheyAre: 0 });
  });

  test("typing in Change to announces the count it changes once typing stops, in the update that shows it", () => {
    typeQuery(editor, DATE);
    vi.advanceTimersByTime(FIND_DELAY_MS);
    const searched = editor.store.get().announcement;
    const counted = (state: EditorState) => countSentence(findCount(state), PSEUDO_SENTENCES);
    const typing = updatesOf(() => {
      typeReplacement(editor, "14 J");
      typeReplacement(editor, "14 Juné");
    });
    const stopped = updatesOf(() => vi.advanceTimersByTime(FIND_DELAY_MS));
    expect(stopped.map((state) => [counted(state), state.announcement.text])).toEqual([
      ["2 PLACES WILL LOOK DIFFERENT", "2 PLACES WILL LOOK DIFFERENT"],
    ]);
    // Not with each letter: echoed, and neither counted nor heard yet.
    expect(typing.map((state) => [state.find.replacement, counted(state), state.announcement])).toEqual([
      ["14 J", searched.text, searched],
      ["14 Juné", searched.text, searched],
    ]);

    // A count typing leaves as it was isn't heard again.
    typeReplacement(editor, "14 Junée");
    vi.advanceTimersByTime(FIND_DELAY_MS);
    expect(editor.store.get().announcement).toBe(stopped[0]?.announcement);
  });

  test("typing in Change to before the find has run finds and counts both once typing stops", () => {
    typeQuery(editor, DATE);
    typeReplacement(editor, "14 Juné");
    vi.advanceTimersByTime(FIND_DELAY_MS);
    expect(found()).toEqual([[0, ["first", "similar"]]]);
    expect(editor.store.get().announcement.text).toBe("2 PLACES WILL LOOK DIFFERENT");
  });

  test("a count Change to leaves is heard where it differs from the one last heard, though shown already", () => {
    typeQuery(editor, DATE);
    vi.advanceTimersByTime(FIND_DELAY_MS);
    expect(editor.store.get().announcement.text).toBe("2 PLACES, 1 OF THEM WILL LOOK DIFFERENT");
    // Typed past its room on the page: the count shown moves, and typing says why, not the count.
    edit(editor, "first", null);
    typeInto(editor, `was ${DATE} and the whole of the year that follows it`);
    typeReplacement(editor, "14 June");
    vi.advanceTimersByTime(FIND_DELAY_MS);
    expect(editor.store.get().announcement.text).toBe("2 PLACES WILL LOOK DIFFERENT");
  });

  test("Change all pressed before the count caught up, every place to be left empty, says so and changes nothing", () => {
    typeQuery(editor, `was ${DATE}`);
    vi.advanceTimersByTime(FIND_DELAY_MS);
    typeReplacement(editor, "was 14 June");
    vi.advanceTimersByTime(FIND_DELAY_MS);
    typeReplacement(editor, " ");
    const before = editor.store.get().history;
    changeAll(editor);
    const pressed = editor.store.get();
    expect(pressed.history).toBe(before);
    expect(countSentence(findCount(pressed), PSEUDO_SENTENCES)).toBe("1 PLACE CAN'T BE LEFT EMPTY");
    expect(pressed.announcement.text).toBe("1 PLACE CAN'T BE LEFT EMPTY");
    // Nothing left waiting to say it again.
    expect(updatesOf(() => vi.advanceTimersByTime(FIND_DELAY_MS))).toEqual([]);
  });

  test("closed while typing in Find, the bar opens again finding what it shows", () => {
    typeQuery(editor, DATE);
    closeFind(editor);
    openFind(editor);
    expect(found()).toEqual([[0, ["first", "similar"]]]);
  });

  test("closed while typing in Change to, the bar opens again counting what it shows", () => {
    typeQuery(editor, DATE);
    vi.advanceTimersByTime(FIND_DELAY_MS);
    typeReplacement(editor, "14 Juné");
    closeFind(editor);
    openFind(editor);
    expect(findCount(editor.store.get())).toEqual({ places: 2, mismatches: 2, leftAsTheyAre: 0 });
  });

  test("counts the span being typed in by the words typed, as Change all changes them", () => {
    edit(editor, "first", null);
    const typed = `was ${DATE} and the whole of the year that follows it`;
    typeInto(editor, typed);
    typeQuery(editor, DATE);
    vi.advanceTimersByTime(FIND_DELAY_MS);
    typeReplacement(editor, "14 June");
    vi.advanceTimersByTime(FIND_DELAY_MS);
    // Saved, its words changed fit its room; as typed, they run past it.
    expect(findCount(editor.store.get())).toEqual({ places: 2, mismatches: 2, leftAsTheyAre: 0 });
    changeAll(editor);
    expect(editor.store.get().reading.spans.get("first")?.text).toBe(typed.replace(DATE, "14 June"));
  });

  test("counts a place by the verdict its change would get, as the chip says it of that edit", () => {
    // Its copy of the font lacks "€", so judged on its own words it's a similar font's; the copy draws every other letter.
    const lacking = aSpan({
      id: "total",
      text: "Total 14 €",
      fidelity: "substitute",
      substitute: "Liberation Serif Regular",
    });
    const font = aFont("Times-Roman", {
      substitute: "Liberation Serif Regular",
      glyphs: halfWideGlyphs("Total 0123456789"),
    });
    editor = createEditor(new File(["%PDF-"], "invoice.pdf"), aDoc({ spans: [lacking], fonts: [font] }), 2);
    openFind(editor);
    typeQuery(editor, "14 €");
    vi.advanceTimersByTime(FIND_DELAY_MS);
    // Nothing to change to yet: judged on the words it reads now, as the chip judges them as an edit starts.
    expect(findCount(editor.store.get())).toEqual({ places: 1, mismatches: 1, leftAsTheyAre: 0 });

    typeReplacement(editor, "2");
    vi.advanceTimersByTime(FIND_DELAY_MS);
    expect(findCount(editor.store.get())).toEqual({ places: 1, mismatches: 0, leftAsTheyAre: 0 });
    changeAll(editor);
    expect(editor.store.get().reading.spans.get("total")?.text).toBe("Total 2");
    expect(chipWarningOf("total", "Total 2")).toBeNull();
    expect(chipWarningOf("total", "Total 14 €")).toBe("a similar font");
  });

  test("counts a place in a font it can't measure by the span's own verdict, as the chip says it", () => {
    // Its font isn't among the document's: the chip has nothing to work an edit out from, though a render landed.
    const unlisted = aSpan({
      id: "total",
      text: "Total 14 €",
      font: "Unlisted",
      fidelity: "substitute",
      substitute: "Liberation Serif Regular",
    });
    editor = createEditor(new File(["%PDF-"], "invoice.pdf"), aDoc({ spans: [unlisted] }), 2);
    change(editor, { kind: "add", edits: [aReplace("total", "Total 2")] });
    const { reading } = editor.store.get();
    editor.store.set({
      drawn: { ...NOTHING_DRAWN, from: new Map([[0, reading]]), fits: new Map([[0, { total: aFit() }]]) },
    });
    openFind(editor);
    typeQuery(editor, "2");
    vi.advanceTimersByTime(FIND_DELAY_MS);
    typeReplacement(editor, "2");
    vi.advanceTimersByTime(FIND_DELAY_MS);
    expect(findCount(editor.store.get())).toEqual({ places: 1, mismatches: 1, leftAsTheyAre: 0 });
    changeAll(editor);
    expect(chipWarningOf("total", "Total 2")).toBe("a similar font");
  });

  test("says the count in the server's words, in the form each count takes", () => {
    const sentence = (places: number, mismatches: number, leftAsTheyAre = 0) =>
      countSentence({ places, mismatches, leftAsTheyAre }, PSEUDO_SENTENCES);
    expect(sentence(0, 0)).toBe("NOTHING MATCHES");
    expect(sentence(1, 0)).toBe("1 PLACE WILL LOOK IDENTICAL");
    expect(sentence(14, 3)).toBe("14 PLACES, 3 OF THEM WILL LOOK DIFFERENT");
    expect(sentence(2, 2)).toBe("2 PLACES WILL LOOK DIFFERENT");
    expect(sentence(2, 0, 2)).toBe("2 PLACES CAN'T BE LEFT EMPTY");
    expect(sentence(14, 0, 2)).toBe("14 PLACES, 2 OF THEM CAN'T BE LEFT EMPTY");
    expect(sentence(14, 3, 2)).toBe("14 PLACES, 3 OF THEM WILL LOOK DIFFERENT / 2 OF THEM CAN'T BE LEFT EMPTY");
  });

  test("keeps apart the part of the count that warns, for --warn, and nothing else", () => {
    const shown = (places: number, mismatches: number) =>
      countShown({ places, mismatches, leftAsTheyAre: 0 }, PSEUDO_SENTENCES);
    expect(shown(14, 3)).toEqual({ plain: "14 PLACES, ", warned: "3 OF THEM WILL LOOK DIFFERENT" });
    expect(shown(2, 2)).toEqual({ plain: "", warned: "2 PLACES WILL LOOK DIFFERENT" });
    expect(shown(1, 0)).toEqual({ plain: "1 PLACE WILL LOOK IDENTICAL", warned: "" });
  });

  test("a reading persisted before the count's sentences were sent shows no count, and never half of one", () => {
    const lacking = (...keys: (keyof Sentences)[]) => {
      const stale = structuredClone(PSEUDO_SENTENCES);
      for (const key of keys) Reflect.deleteProperty(stale, key);
      return stale;
    };
    const nothing = { plain: "", warned: "" };
    expect(countShown({ places: 14, mismatches: 3, leftAsTheyAre: 0 }, lacking("find_some_different"))).toEqual(
      nothing,
    );
    expect(countShown({ places: 14, mismatches: 3, leftAsTheyAre: 0 }, lacking("find_places"))).toEqual(nothing);
    expect(countShown({ places: 0, mismatches: 0, leftAsTheyAre: 0 }, lacking("find_nothing"))).toEqual(nothing);
    expect(countShown({ places: 2, mismatches: 2, leftAsTheyAre: 0 }, lacking("find_different"))).toEqual(nothing);
    expect(countShown({ places: 2, mismatches: 0, leftAsTheyAre: 2 }, lacking("find_empty"))).toEqual(nothing);
    expect(countShown({ places: 14, mismatches: 0, leftAsTheyAre: 2 }, lacking("find_some_empty"))).toEqual(nothing);
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
    expect(announcement.text).toBe("CHANGED 2 PLACES.");

    undo(editor);
    expect(editor.store.get().reading.spans.get("first")).toBeUndefined();
    expect(editor.store.get().reading.spans.get("similar")).toBeUndefined();
  });

  test("Change all of one place says so in the form one takes", () => {
    typeQuery(editor, `was ${DATE}`);
    typeReplacement(editor, "was 14 June");
    changeAll(editor);
    expect(editor.store.get().find.changed).toBe(1);
    expect(editor.store.get().announcement.text).toBe("CHANGED 1 PLACE.");
  });

  test("after Change all said what it changed, the count Change to leaves is heard, though it was heard before", () => {
    typeQuery(editor, DATE);
    typeReplacement(editor, `${DATE} and`);
    vi.advanceTimersByTime(FIND_DELAY_MS);
    const before = editor.store.get().announcement.text;
    changeAll(editor);
    expect(editor.store.get().announcement.text).toBe("CHANGED 2 PLACES.");
    // Still found, so the bar swaps back to the count, as it was before the change.
    typeReplacement(editor, `${DATE} too`);
    vi.advanceTimersByTime(FIND_DELAY_MS);
    expect(countSentence(findCount(editor.store.get()), PSEUDO_SENTENCES)).toBe(before);
    expect(editor.store.get().announcement.text).toBe(before);
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

  test("Change all leaves as it was a place it would leave empty, as typing does, and the count says so first", () => {
    const spans = [aSpan({ id: "date", text: DATE }), aSpan({ id: "first", text: `was ${DATE}` })];
    const doc = aDoc({ sentences: PSEUDO_SENTENCES, spans, fonts: DOC.fonts });
    editor = createEditor(new File(["%PDF-"], "contract.pdf"), doc, 2);
    openFind(editor);
    typeQuery(editor, DATE);
    vi.advanceTimersByTime(FIND_DELAY_MS);
    // Only spaces: "date" would read nothing, which typing's own test calls empty.
    typeReplacement(editor, "  ");
    vi.advanceTimersByTime(FIND_DELAY_MS);
    expect(countSentence(findCount(editor.store.get()), PSEUDO_SENTENCES)).toBe(
      "2 PLACES, 1 OF THEM CAN'T BE LEFT EMPTY",
    );
    changeAll(editor);
    const { reading, announcement } = editor.store.get();
    expect(reading.spans.get("date")).toBeUndefined();
    expect(reading.spans.get("first")?.text).toBe("was   ");
    expect(announcement.text).toBe("CHANGED 1 PLACE.");
    // Still a match, so still marked: the count said it stays.
    expect(found()).toEqual([[0, ["date"]]]);
  });

  test("Change all changes nothing when every place would be left empty, and the count says so", () => {
    typeQuery(editor, `was ${DATE}`);
    vi.advanceTimersByTime(FIND_DELAY_MS);
    typeReplacement(editor, " ");
    vi.advanceTimersByTime(FIND_DELAY_MS);
    expect(countSentence(findCount(editor.store.get()), PSEUDO_SENTENCES)).toBe("1 PLACE CAN'T BE LEFT EMPTY");
    const before = editor.store.get().history;
    changeAll(editor);
    expect(editor.store.get().history).toBe(before);
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
    expect(editor.store.get().announcement.text).toBe("NOTHING MATCHES");
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
      const edits = { spans: [reading], newTexts: [] };
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
    expect(placeAt(editor.store.get())).toBe(2);
  });

  test("steps through the places the count counts, the span being typed in among them", () => {
    typeQuery(editor, DATE);
    vi.advanceTimersByTime(FIND_DELAY_MS);
    edit(editor, "other", null);
    typeInto(editor, `${DATE} now`);
    expect(findCount(editor.store.get()).places).toBe(3);
    stepFind(editor, -1);
    expect(current()).toBe("other");
    expect(editor.store.get().announcement.text).toBe("3 of 3");
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
    expect(placeAt(editor.store.get())).toBeNull();
  });

  test("a step while Change to is still being typed counts it first, heard with the place in the update that shows it", () => {
    typeQuery(editor, DATE);
    vi.advanceTimersByTime(FIND_DELAY_MS);
    typeReplacement(editor, "14 Juné");
    const stepped = updatesOf(() => stepFind(editor, 1));
    expect(
      stepped.map((state) => [countSentence(findCount(state), PSEUDO_SENTENCES), state.announcement.text]),
    ).toEqual([["2 PLACES WILL LOOK DIFFERENT", "2 PLACES WILL LOOK DIFFERENT / 1 of 2"]]);
    expect(updatesOf(() => vi.advanceTimersByTime(FIND_DELAY_MS))).toEqual([]);
    // Heard once: the next step says only its place, as does one whose Change to leaves the count as heard.
    stepFind(editor, 1);
    expect(editor.store.get().announcement.text).toBe("2 of 2");
    typeReplacement(editor, "14 Junée");
    stepFind(editor, 1);
    expect(editor.store.get().announcement.text).toBe("1 of 2");
  });

  test("a step before Find has found counts it first, heard with the place in the update that shows it", () => {
    typeQuery(editor, DATE);
    const stepped = updatesOf(() => stepFind(editor, 1));
    expect(stepped.map((state) => [state.find.current, state.announcement.text])).toEqual([
      ["first", "2 PLACES, 1 OF THEM WILL LOOK DIFFERENT / 1 of 2"],
    ]);
  });

  test("with find shut, nothing is gone to", () => {
    closeFind(editor);
    stepFind(editor, 1);
    expect(current()).toBeNull();
  });
});
