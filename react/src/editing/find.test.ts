import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { render } from "../api/client";
import type { Render } from "../api/types";
import { aDoc, aFont, aSpan } from "../fixtures";
import { FIND_DELAY_MS } from "./constants";
import { change, createEditor, type Editor, undo } from "./editor";
import {
  changeAll,
  closeFind,
  countSaid,
  countShown,
  type FindOptions,
  findCount,
  openFind,
  placesAcross,
  placesIn,
  replacedIn,
  setMatchCase,
  setWholeWords,
  typeQuery,
  typeReplacement,
} from "./find";

vi.mock(import("../api/client"), async (original) => ({ ...(await original()), render: vi.fn() }));

// Every letter these tests type, as wide as each other, so "14 June" fits where "14 March" was.
const glyphs = Object.fromEntries([..." 0123456789MarchJunewsoifdtly"].map((letter) => [letter, 500]));
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
  vi.mocked(render).mockReturnValue(new Promise<Render>(() => undefined));
  editor = createEditor(new File(["%PDF-"], "contract.pdf"), DOC, 2);
  change(editor, { kind: "add", edits: [{ kind: "redact", span_id: "redacted" }] });
  openFind(editor);
});

afterEach(() => {
  vi.useRealTimers();
});

const found = () => [...editor.store.get().find.hits].map(([page, spans]) => [page, [...spans.keys()]]);

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
    change(editor, { kind: "add", edits: [{ kind: "redact", span_id: "other" }] });
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
    const marks = editor.store.get().find.hits.get(0);
    change(editor, { kind: "add", edits: [{ kind: "replace", span_id: "other", text: "still nothing" }] });
    expect(editor.store.get().find.hits.get(0)).toBe(marks);
  });

  test("closing clears the marks and stops a find still waiting to run", () => {
    typeQuery(editor, DATE);
    closeFind(editor, { fromInside: true });
    vi.advanceTimersByTime(FIND_DELAY_MS);
    const { find } = editor.store.get();
    expect(find.open).toBe(false);
    expect(find.hits.size).toBe(0);
    expect(find.closed).toBe(1);
  });
});

describe("matching", () => {
  const ANY = { matchCase: false, wholeWords: false };
  /** `text` with what reads `find` changed `to`, as "Find" matches with `options`. */
  const change = (text: string, { find, to, ...options }: { find: string; to: string } & Partial<FindOptions>) =>
    replacedIn(text, placesIn(text, find, { ...ANY, ...options }), to);

  test("a space, quote or dash the keyboard can't type matches the one it can", () => {
    const line = "Paid on 14\u00a0March to the Client\u2019s bank \u2013 in full";
    expect(change(line, { find: "14 March", to: "2 April" })).toBe(
      "Paid on 2\u00a0April to the Client\u2019s bank \u2013 in full",
    );
    expect(change(line, { find: "Client's", to: "Customer's" })).toBe(
      "Paid on 14\u00a0March to the Customer\u2019s bank \u2013 in full",
    );
    expect(change(line, { find: "bank - in", to: "bank, in" })).toBe(
      "Paid on 14\u00a0March to the Client\u2019s bank, in full",
    );
  });

  test("a change is spelled as the page spelled what it replaces: its spaces, quotes and dashes, where all were one kind", () => {
    expect(change("from 2019\u20132020", { find: "2019-2020", to: "2020-2021" })).toBe("from 2020\u20132021");
    // A no-break space and a plain one in what was found: nothing to say which the change wants.
    expect(change("14\u00a0March 2026", { find: "14 March 2026", to: "2 April 2027" })).toBe("2 April 2027");
    expect(change("the Client\u2019s", { find: "Client\u2019s", to: "Customer's" })).toBe("the Customer\u2019s");
  });

  test("letters of either case match, unless Match case", () => {
    expect(change("March and march", { find: "MARCH", to: "June" })).toBe("June and June");
    expect(change("March and march", { find: "march", to: "june", matchCase: true })).toBe("March and june");
  });

  test("a match inside a word counts, unless Whole words", () => {
    expect(change("Ann signed the Annual report", { find: "Ann", to: "Bea" })).toBe("Bea signed the Beaual report");
    expect(change("Ann signed the Annual report", { find: "Ann", to: "Bea", wholeWords: true })).toBe(
      "Bea signed the Annual report",
    );
  });

  test("a ligature reads as its letters, and is only ever changed whole", () => {
    expect(change("\ufb01nance", { find: "finance", to: "funds" })).toBe("funds");
    // Half of one would leave the other letter's half behind.
    expect(placesIn("\ufb01nance", "f", ANY)).toEqual([]);
  });

  test("an unseen hyphen or space between letters doesn't stop them matching, and goes with what's changed", () => {
    expect(change("the infor\u00admation sheet", { find: "information", to: "data" })).toBe("the data sheet");
    expect(change("a\u200bb", { find: "ab", to: "c" })).toBe("c");
  });

  test("a place found sits across its span by its letters' widths, so only the words found are marked", () => {
    // Ten 5 pt letters across 50 pt: "cdef" is the third to the sixth.
    const line = aSpan({ id: "line", text: "abcdefghij", size: 10, bbox: { x0: 100, y0: 0, x1: 150, y1: 12 } });
    const glyphs = Object.fromEntries([..."abcdefghij"].map((letter) => [letter, 500]));
    const places = placesIn(line.text, "cdef", ANY);
    expect(placesAcross(line, { text: line.text, places, glyphs })).toEqual([{ x0: 110, x1: 130 }]);
  });

  test("nothing to find finds nothing", () => {
    expect(placesIn("anything", "", ANY)).toEqual([]);
    expect(placesIn("anything", "\u00ad", ANY)).toEqual([]);
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
});
