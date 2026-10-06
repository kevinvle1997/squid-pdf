import { describe, expect, test } from "vitest";
import { aSpan } from "../fixtures";
import { changedWordsIn, notePlaces } from "./margin";
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
  expect(notePlaces(changes, 18)).toEqual([305, 105, 123]);
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
