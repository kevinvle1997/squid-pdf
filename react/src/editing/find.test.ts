import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { renderStrips } from "../api/client";
import type { Render } from "../api/types";
import { aDoc, aFont, aRedact, aReplace, aSpan, halfWideGlyphs } from "../fixtures";
import { FIND_DELAY_MS } from "./constants";
import { change, createEditor, type Editor, undo } from "./editor";
import {
  changeAll,
  closeFind,
  countSaid,
  countShown,
  findCount,
  matchesAcross,
  openFind,
  setMatchCase,
  setWholeWords,
  typeQuery,
  typeReplacement,
} from "./find";
import { matchesIn } from "./matching";

vi.mock(import("../api/client"), async (original) => ({ ...(await original()), renderStrips: vi.fn() }));

// Every letter these tests type, as wide as each other, so "14 June" fits where "14 March" was.
const glyphs = halfWideGlyphs(" 0123456789MarchJunewsoifdtly");
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

describe("find", () => {
  test("finds once typing stops, in every span that reads it but a form field's or a redaction's", () => {
    typeQuery(editor, DATE);
    expect(editor.store.get().find.query).toBe(DATE);
    expect(found()).toEqual([]);

    vi.advanceTimersByTime(FIND_DELAY_MS);
    expect(found()).toEqual([[0, ["first", "similar"]]]);
    expect(editor.store.get().said.text).toBe("2 places, 1 of them will look different");
  });

  test("counts a place as looking different for its font, and for the words it would read", () => {
    typeQuery(editor, DATE);
    vi.advanceTimersByTime(FIND_DELAY_MS);
    expect(findCount(editor.store.get())).toEqual({ places: 2, unlike: 1 });

    typeReplacement(editor, "14 June");
    expect(findCount(editor.store.get())).toEqual({ places: 2, unlike: 1 });
    // A letter the font hasn't got, and a line that runs past its room.
    typeReplacement(editor, "14 Juné");
    expect(findCount(editor.store.get())).toEqual({ places: 2, unlike: 2 });
    typeReplacement(editor, "14 June and the whole of the year that follows it");
    expect(findCount(editor.store.get())).toEqual({ places: 2, unlike: 2 });
  });

  test("says the count in whole words", () => {
    expect(countSaid({ places: 0, unlike: 0 })).toBe("Nothing matches");
    expect(countSaid({ places: 1, unlike: 0 })).toBe("1 place will look identical");
    expect(countSaid({ places: 14, unlike: 3 })).toBe("14 places, 3 of them will look different");
    expect(countSaid({ places: 2, unlike: 2 })).toBe("2 places will look different");
  });

  test("keeps apart the part of the count that warns, for --warn, and nothing else", () => {
    expect(countShown({ places: 14, unlike: 3 })).toEqual({
      plain: "14 places, ",
      warned: "3 of them will look different",
    });
    expect(countShown({ places: 2, unlike: 2 })).toEqual({ plain: "", warned: "2 places will look different" });
    expect(countShown({ places: 1, unlike: 0 })).toEqual({ plain: "1 place will look identical", warned: "" });
  });

  test("Change all is one step: every place changes together, and one undo puts them all back", () => {
    typeQuery(editor, DATE);
    typeReplacement(editor, "14 June");
    // Pressed before the find has run: it changes what "Find" shows.
    changeAll(editor);
    const { reading, find, said } = editor.store.get();
    expect(reading.spans.get("first")?.text).toBe("was 14 June");
    expect(reading.spans.get("similar")?.text).toBe("14 June too");
    expect(find.changed).toBe(2);
    expect(found()).toEqual([]);
    expect(said.text).toBe("Changed 2 places.");

    undo(editor);
    expect(editor.store.get().reading.spans.get("first")).toBeUndefined();
    expect(editor.store.get().reading.spans.get("similar")).toBeUndefined();
  });

  test("what Change all did stops being said once anything else changes: undo would take that, not the places", () => {
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
    expect(editor.store.get().said.text).toBe("Nothing matches");
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
    expect(matchesAcross(line, { text: line.text, matches, glyphs })).toEqual([{ x0: 110, x1: 130 }]);
  });
});
