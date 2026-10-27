import { describe, expect, test } from "vitest";
import { anInsert, aSpan } from "../fixtures";
import { NEW_TEXT_ASCENT_EM, NEW_TEXT_DESCENT_EM } from "./constants";
import { changedWordsIn, insertedWordsIn, marginNotesOf, noteOf, notePlaces } from "./margin";
import type { SpanReading } from "./project";

const changeAt = (id: string, y0: number): SpanReading => ({
  span: aSpan({ id, bbox: { x0: 72, y0, x1: 200, y1: y0 + 10 } }),
  text: "now",
  replaced: true,
  strategy: undefined,
  redacted: false,
});

test("a note sits level with its line, and one that would overlap the note above moves down", () => {
  // Given out of order: the places come back in the order given.
  const changes = [changeAt("low", 300), changeAt("top", 100), changeAt("close", 104)];
  expect(
    notePlaces(
      changes.map(({ span }) => span.bbox),
      18,
    ),
  ).toEqual([305, 105, 123]);
});

describe("what a note shows of the original", () => {
  test("the words an edit changed, whole, not the line from its start", () => {
    const line = "This agreement is made on 14 March 2026 between";
    expect(changedWordsIn(line, "This agreement is made on 2 April 2026 between")).toBe("14 March");
    expect(changedWordsIn(line, "This agreement is made on 14 Marching 2026 between")).toBe("March");
  });

  test("words only added: the word they went in beside", () => {
    expect(changedWordsIn("was here", "was here now")).toBe("here");
    expect(changedWordsIn("was here", "now was here")).toBe("was");
    expect(changedWordsIn("was here", "was now here")).toBe("was");
  });

  test("a line changed throughout, or emptied, is the whole line", () => {
    expect(changedWordsIn("was here", "is there")).toBe("was here");
    expect(changedWordsIn("was here", "")).toBe("was here");
  });

  test("a letter outside the basic plane is never cut in half", () => {
    expect(changedWordsIn("a 😀 b", "a 😁 b")).toBe("😀");
  });
});

describe("what a note says", () => {
  test("a change's note strikes the words it took out, and is named for what pressing it undoes", () => {
    const change = {
      ...changeAt("s", 100),
      span: aSpan({ id: "s", text: "Paid on 14 March" }),
      text: "Paid on 2 April",
    };
    expect(noteOf(change)).toEqual({
      label: "Undo: “Paid on 2 April” goes back to “Paid on 14 March”",
      struck: "14 March",
      inserted: null,
      was: "Paid on 14 March",
      now: "Paid on 2 April",
    });
  });

  test("a redaction's strikes every word, since it takes them all out", () => {
    const redaction = { ...changeAt("s", 100), span: aSpan({ id: "s", text: "John Smith" }), redacted: true };
    expect(noteOf(redaction)).toEqual({
      label: "Undo: “John Smith” is no longer redacted",
      struck: "John Smith",
      inserted: null,
      was: "John Smith",
      now: "Redacted",
    });
  });

  test("words only added are shown as added, not as the word kept beside them struck", () => {
    const change = {
      ...changeAt("s", 100),
      span: aSpan({ id: "s", text: "SERVICES AGREEMENT" }),
      text: "SERVICES AGREEMENT ✓",
    };
    expect(noteOf(change)).toMatchObject({ struck: "AGREEMENT", inserted: "✓" });
    expect(insertedWordsIn("was here", "now was here")).toBe("now");
    expect(insertedWordsIn("was here", "was very much here")).toBe("very much");
    // A word changed or taken out: nothing was only added.
    expect(insertedWordsIn("was here", "is here now")).toBeNull();
    expect(insertedWordsIn("was here", "was")).toBeNull();
  });
});

describe("a page's margin notes", () => {
  test("are each changed span's, then each piece of new text's, level with its line, a proof's caret before its words", () => {
    const newText = { id: 3, newTextId: 2, edit: anInsert({ text: "Signed", origin: [72, 400], size: 10 }) };
    const notes = marginNotesOf([changeAt("one", 100)], [newText]);
    expect(notes.map(({ key }) => key)).toEqual(["one", "new text 2"]);
    expect(notes[1]?.line).toMatchObject({ y0: 400 - 10 * NEW_TEXT_ASCENT_EM, y1: 400 + 10 * NEW_TEXT_DESCENT_EM });
    expect(notes[1]?.note).toMatchObject({ label: "Undo: take out “Signed”", inserted: "Signed", was: "" });
  });
});
