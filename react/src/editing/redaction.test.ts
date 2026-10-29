import { beforeEach, describe, expect, test, vi } from "vitest";
import shared from "../../../tests/editing/redacted_words_cases.json";
import { renderStrips } from "../api/client";
import type { Render } from "../api/types";
import { aDoc, aRedact, aSpan, PSEUDO_SENTENCES, SENTENCES } from "../fixtures";
import { commandFor } from "./commands";
import { createEditor, type Editor, redo, undo } from "./editor";
import { editAnyway, keepRedaction, redact, takesSelection, wordsAround, wordsSelected } from "./redaction";
import { edit, finish, typeInto } from "./typing";
import { fill } from "./words";

vi.mock(import("../api/client"), async (original) => ({ ...(await original()), renderStrips: vi.fn() }));

// Its sentences are the server's, in a language made up for tests, so any the browser writes itself stands out.
const DOC = aDoc({
  sentences: PSEUDO_SENTENCES,
  spans: [
    aSpan({ id: "name", text: "John Smith" }),
    aSpan({ id: "other", text: "Acme" }),
    aSpan({ id: "card", text: "Card 4421 then 9918" }),
  ],
});
const AT = { spanId: null, page: null, newTextId: null, marginNote: false };

let editor: Editor;

beforeEach(() => {
  vi.mocked(renderStrips)
    .mockReset()
    .mockReturnValue(new Promise<Render>(() => undefined));
  editor = createEditor(new File(["%PDF-"], "contract.pdf"), DOC, 2);
});

const pressed = (keys: Partial<KeyboardEvent>, typing = false, selecting = false) =>
  commandFor({ key: "", metaKey: false, ctrlKey: false, shiftKey: false, altKey: false, ...keys }, typing, {
    selecting,
  });

describe("redacting", () => {
  test("Cmd+Delete on a span redacts the whole of it, says so, and keeps focus there", () => {
    pressed({ key: "Backspace", metaKey: true })?.run(editor, { ...AT, spanId: "name", selected: null });
    const state = editor.store.get();
    expect(state.reading.edits).toEqual([{ kind: "redact", span_id: "name" }]);
    expect(state.reading.spans.get("name")?.redacted).toBe("whole");
    expect(state.announcement.text).toBe("REDACTED “John Smith”.");
    expect(state.focusTo).toEqual({ spanId: "name" });
    expect(renderStrips).toHaveBeenCalledTimes(1);
  });

  test("while typing, Cmd or Ctrl with Backspace or Delete is the field's own, deleting a word or a line", () => {
    edit(editor, "name", null);
    typeInto(editor, "Jane Smith");
    for (const key of ["Backspace", "Delete"]) {
      expect(pressed({ key, ctrlKey: true }, true)).toBeUndefined();
      expect(pressed({ key, metaKey: true }, true)).toBeUndefined();
    }
  });

  test("redacting while typing takes the span typed in, whatever part is selected, and the typed words go", () => {
    edit(editor, "name", null);
    typeInto(editor, "Jane Smith");
    redact(editor, null);
    const state = editor.store.get();
    expect(state.draft).toBeNull();
    expect(state.reading.edits).toEqual([{ kind: "redact", span_id: "name" }]);
  });

  test("a span already redacted isn't redacted again, and a key pressed on no span does nothing", () => {
    redact(editor, "name");
    redact(editor, "name");
    redact(editor, null);
    expect(editor.store.get().reading.edits).toHaveLength(1);
  });

  test("undo takes a redaction off, as it does any edit", () => {
    redact(editor, "name");
    undo(editor);
    expect(editor.store.get().reading.edits).toEqual([]);
  });
});

describe("redacting some words of a span", () => {
  test.each(shared.widened)("a range takes whole words as the server widens one: $what", ({ text, range, words }) => {
    expect(wordsAround(text, [range[0] ?? 0, range[1] ?? 0])).toEqual(words);
  });

  test("a selection is in the field's units, and what it takes in code points, as the server counts", () => {
    // A letter past the first 65,536 is two units in the field, one code point on the server.
    expect(wordsSelected("\u{1D400}x 4421", [4, 6])).toEqual([3, 7]);
  });

  test("Cmd+Delete over words selected in the field redacts only them, says which, and the field shuts", () => {
    edit(editor, "card", null);
    pressed({ key: "Backspace", metaKey: true }, true, true)?.run(editor, { ...AT, selected: [6, 8] });
    const state = editor.store.get();
    expect(state.reading.edits).toEqual([aRedact("card", { range: [5, 9] })]);
    expect(state.reading.spans.get("card")?.redacted).toEqual([{ start: 5, end: 9 }]);
    expect(state.announcement.text).toBe("REDACTED “4421”.");
    expect(state.draft).toBeNull();
    expect(state.focusTo).toEqual({ spanId: "card" });
  });

  test("the key is the redaction's only over words the field reads as the page does", () => {
    edit(editor, "card", null);
    expect(takesSelection(editor.store.get(), [6, 8])).toBe(true);
    expect(takesSelection(editor.store.get(), [4, 5])).toBe(false);
    typeInto(editor, "Card 4421 then 9919");
    expect(takesSelection(editor.store.get(), [6, 8])).toBe(false);
    expect(pressed({ key: "Backspace", ctrlKey: true }, true, false)).toBeUndefined();
  });

  test("with every word selected, or over words typed, the whole span is redacted", () => {
    edit(editor, "card", null);
    redact(editor, null, { selected: [0, 19] });
    expect(editor.store.get().reading.edits).toEqual([aRedact("card")]);
    undo(editor);
    edit(editor, "card", null);
    typeInto(editor, "Card 4421 then 9919");
    redact(editor, null, { selected: [5, 9] });
    expect(editor.store.get().reading.edits).toEqual([aRedact("card")]);
  });

  test("more words are a redaction of their own; words redacted already add none, and are said to be", () => {
    edit(editor, "card", null);
    redact(editor, null, { selected: [5, 9] });
    edit(editor, "card", null);
    redact(editor, null, { selected: [15, 19] });
    edit(editor, "card", null);
    redact(editor, null, { selected: [6, 7] });
    const { reading, announcement } = editor.store.get();
    expect(announcement.text).toBe(fill(PSEUDO_SENTENCES.already_redacted, { text: "4421" }));
    expect(reading.edits).toEqual([aRedact("card", { range: [5, 9] }), aRedact("card", { range: [15, 19] })]);
    expect(reading.spans.get("card")?.redacted).toEqual([
      { start: 5, end: 9 },
      { start: 15, end: 19 },
    ]);
  });

  test("undo and redo of a redaction say the words put back or taken out, not the line", () => {
    edit(editor, "card", null);
    redact(editor, null, { selected: [5, 9] });
    undo(editor);
    expect(editor.store.get().announcement.text).toBe("PUT BACK “4421”.");
    redo(editor);
    expect(editor.store.get().announcement.text).toBe("REDACTED “4421”.");
    // All of it, over the words redacted: undone, they stay, and the rest comes back.
    redact(editor, "card");
    undo(editor);
    expect(editor.store.get().reading.spans.get("card")?.redacted).toEqual([{ start: 5, end: 9 }]);
    expect(editor.store.get().announcement.text).toBe("PUT BACK “Card … then 9918”.");
  });

  test("Enter, then Cmd+Delete over the line it selects whole, redacts all of it, as an emptied line is no edit", () => {
    edit(editor, "card", null);
    pressed({ key: "Backspace", metaKey: true }, true, true)?.run(editor, { ...AT, selected: [0, 19] });
    expect(editor.store.get().reading.edits).toEqual([aRedact("card")]);
  });
});

describe("editing a redacted span", () => {
  test("asks first, in the server's words, and opens nothing yet", () => {
    redact(editor, "name");
    edit(editor, "name", { atPt: 12, selects: "caret" });
    const state = editor.store.get();
    expect(state.unredacting).toEqual({ spanId: "name", press: { atPt: 12, selects: "caret" }, typed: null });
    expect(state.draft).toBeNull();
    expect(state.announcement.text).toBe(SENTENCES.undo_redaction);
  });

  test("a span redacted in part opens to select more; typing asks first, the field shut, the words not typed", () => {
    edit(editor, "card", null);
    redact(editor, null, { selected: [5, 9] });
    edit(editor, "card", null);
    expect(editor.store.get().draft).toMatchObject({ spanId: "card", text: "Card 4421 then 9918" });

    typeInto(editor, "Card 4421 then 9918!");

    const state = editor.store.get();
    expect(state.draft).toBeNull();
    expect(state.reading.edits).toEqual([aRedact("card", { range: [5, 9] })]);
    expect(state.unredacting).toEqual({
      spanId: "card",
      press: null,
      typed: { text: "Card 4421 then 9918!", caret: null },
    });
    expect(state.announcement.text).toBe(SENTENCES.undo_part_redaction);
  });

  test("Edit anyway, asked by typing, takes the redaction out and types what was typed, the caret where it was", () => {
    edit(editor, "card", null);
    redact(editor, null, { selected: [5, 9] });
    edit(editor, "card", null);
    typeInto(editor, "Card 4421 then 9918!", { caret: 20 });

    editAnyway(editor);

    const state = editor.store.get();
    expect(state.reading.edits).toEqual([]);
    expect(state.draft).toMatchObject({ spanId: "card", text: "Card 4421 then 9918!", caret: 20 });
  });

  test("Edit anyway takes the redaction out, and opens the span as it reads without it", () => {
    edit(editor, "name", null);
    typeInto(editor, "Jane Smith");
    finish(editor, true);
    redact(editor, "name");
    edit(editor, "name", null);

    editAnyway(editor);

    const state = editor.store.get();
    expect(state.unredacting).toBeNull();
    expect(state.reading.edits).toEqual([{ kind: "replace", span_id: "name", text: "Jane Smith" }]);
    expect(state.draft).toMatchObject({ spanId: "name", text: "Jane Smith" });
  });

  test("Keep redaction changes nothing, and focus goes back to the span", () => {
    redact(editor, "name");
    edit(editor, "name", null);

    keepRedaction(editor);

    const state = editor.store.get();
    expect(state.unredacting).toBeNull();
    expect(state.reading.edits).toEqual([{ kind: "redact", span_id: "name" }]);
    expect(state.focusTo).toEqual({ spanId: "name" });
  });
});
