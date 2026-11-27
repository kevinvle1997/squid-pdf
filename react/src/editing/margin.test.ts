import { describe, expect, test } from "vitest";
import { aBoldSentence, aDoc, anInsert, aSpan } from "../fixtures";
import { NEW_TEXT_ASCENT_EM, NEW_TEXT_DESCENT_EM } from "./constants";
import { linesBySpan } from "./lines";
import { changedWordsIn, excerptsOf, inLine, insertedWordsIn, marginNotesOf, noteOf, notePlaces } from "./margin";
import type { SpanReading } from "./project";

const changeAt = (id: string, y0: number): SpanReading => ({
  span: aSpan({ id, bbox: { x0: 72, y0, x1: 200, y1: y0 + 10 } }),
  text: "now",
  replaced: true,
  strategy: undefined,
  redacted: null,
});

test("a note sits level with its line, and one that would overlap the note above moves down", () => {
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

describe("what a note shows of the change, over what it became", () => {
  const shown = (original: string, now: string) => {
    const { was, now: became, cut } = excerptsOf(original, now);
    return { was: inLine(was, cut), now: inLine(became, cut) };
  };

  test("the words changed and one either side, cut at spaces, an ellipsis where the line goes on", () => {
    const line = "white box. When you replace a line, the server removes the old glyphs";
    expect(shown(line, line.replace("replace", "replssace"))).toEqual({
      was: "… you replace a …",
      now: "… you replssace a …",
    });
  });

  test("no ellipsis on a side the change reaches the line's end at", () => {
    expect(shown("Paid on 14 March", "Paid on 2 April")).toEqual({ was: "… on 14 March", now: "… on 2 April" });
    expect(shown("Draft terms of sale", "Final terms of sale")).toEqual({ was: "Draft terms …", now: "Final terms …" });
  });

  test("words only added or only taken out: the words around where they were", () => {
    expect(shown("one two three four", "one two and a half three four")).toEqual({
      was: "… two three …",
      now: "… two and a half three …",
    });
    expect(shown("one two three four five", "one two five")).toEqual({
      was: "… two three four five",
      now: "… two five",
    });
  });

  test("a short line is shown whole, and its own spacing stays", () => {
    expect(shown("was  here", "is  here")).toEqual({ was: "was  here", now: "is  here" });
  });

  test("a line emptied shows what it read, and nothing for now", () => {
    expect(shown("was here", "")).toEqual({ was: "was here", now: "" });
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
      struck: ["14 March"],
      inserted: null,
      was: ["on 14 March"],
      now: "on 2 April",
      cut: { before: true, after: false },
    });
  });

  test("a redaction's strikes every word, since it takes them all out", () => {
    const redaction = {
      ...changeAt("s", 100),
      span: aSpan({ id: "s", text: "John Smith" }),
      redacted: "whole" as const,
    };
    expect(noteOf(redaction)).toEqual({
      label: "Undo: “John Smith” is no longer redacted",
      struck: ["John Smith"],
      inserted: null,
      was: ["John Smith"],
      now: "Redacted",
      cut: { before: false, after: false },
    });
  });

  test("a redaction of some words strikes only those, each run on its own, and says they're redacted", () => {
    const span = aSpan({ id: "s", text: "Card 4421 then 9918 ends" });
    const runs = [
      { start: 5, end: 9 },
      { start: 15, end: 19 },
    ];
    const redaction = { ...changeAt("s", 100), span, text: span.text, replaced: false, redacted: runs };
    expect(noteOf(redaction)).toEqual({
      label: "Undo: “4421 … 9918” is no longer redacted",
      struck: ["4421", "9918"],
      inserted: null,
      was: ["4421", "9918"],
      now: "Redacted",
      cut: { before: false, after: false },
    });
  });

  test("words only added are shown as added, not as the word kept beside them struck", () => {
    const change = {
      ...changeAt("s", 100),
      span: aSpan({ id: "s", text: "SERVICES AGREEMENT" }),
      text: "SERVICES AGREEMENT ✓",
    };
    expect(noteOf(change)).toMatchObject({ struck: ["AGREEMENT"], inserted: "✓" });
    expect(insertedWordsIn("was here", "now was here")).toBe("now");
    expect(insertedWordsIn("was here", "was very much here")).toBe("very much");
    expect(insertedWordsIn("was here", "is here now")).toBeNull();
    expect(insertedWordsIn("was here", "was")).toBeNull();
  });
});

describe("a page's margin notes", () => {
  test("are each changed span's, then each piece of new text's, level with its line, a proof's caret before its words", () => {
    const newText = { id: 3, newTextId: 2, edit: anInsert({ text: "Signed", origin: [72, 400], size: 10 }) };
    const notes = marginNotesOf([changeAt("one", 100)], [newText], new Map());
    expect(notes.map(({ key }) => key)).toEqual(["one", "new text 2"]);
    expect(notes[1]?.line).toMatchObject({ y0: 400 - 10 * NEW_TEXT_ASCENT_EM, y1: 400 + 10 * NEW_TEXT_DESCENT_EM });
    expect(notes[1]?.note).toMatchObject({ label: "Undo: take out “Signed”", inserted: "Signed", was: [] });
  });
});

test("a line's runs changed are one note, its words as one line, cut round the change, and one press puts it back", () => {
  const sentence = aBoldSentence();
  const lines = linesBySpan(aDoc(sentence));
  const changes = sentence.spans.map((span) => ({
    span,
    text: span.id === "bold" ? "SPANS" : span.text,
    replaced: span.id === "bold",
    strategy: undefined,
    redacted: null,
  }));
  const [only, ...rest] = marginNotesOf(changes, [], lines);
  expect(rest).toEqual([]);
  expect(only?.key).toBe("before");
  expect(only?.line).toEqual(lines.get("bold")?.bbox);
  expect(only?.note).toMatchObject({ struck: ["spans"], was: ["into spans (one"], now: "into SPANS (one" });
});
