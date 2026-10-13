import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { render } from "../api/client";
import type { Render } from "../api/types";
import { aDoc, aFont, aSpan } from "../fixtures";
import { FIND_DELAY_MS } from "./constants";
import { change, createEditor, type Editor, undo } from "./editor";
import { changeAll, closeFind, countSaid, findCount, openFind, typeQuery, typeReplacement } from "./find";

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

const found = () => [...editor.store.get().find.hits].map(([page, ids]) => [page, [...ids]]);

describe("find", () => {
  test("finds once typing stops, in every span that reads it but a form field's or a redaction's", () => {
    typeQuery(editor, DATE);
    expect(editor.store.get().find.query).toBe(DATE);
    expect(found()).toEqual([]);

    vi.advanceTimersByTime(FIND_DELAY_MS);
    expect(found()).toEqual([[0, ["first", "similar"]]]);
    expect(editor.store.get().said.text).toBe("2 places · 1 will look different");
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
    expect(countSaid({ places: 1, unlike: 0 })).toBe("1 place · all look identical");
    expect(countSaid({ places: 14, unlike: 3 })).toBe("14 places · 3 will look different");
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
