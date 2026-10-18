import { describe, expect, test } from "vitest";
import { aSpan, halfWideGlyphs } from "../fixtures";
import { atPtOf, letterAt, wordAround } from "./caret";

// Every letter half the size wide: 5 pt each at 10 pt.
const glyphs = halfWideGlyphs("abcdefghijklmnopqrstuvwxyz ");

describe("the caret a double press puts down", () => {
  test("a press lands as far into the span, in points, as it is into its mark", () => {
    const span = aSpan({ id: "s", bbox: { x0: 100, y0: 0, x1: 200, y1: 12 } });
    expect(atPtOf(span, { pressX: 50, shownPx: 200 })).toBe(25);
    // Not laid out yet: nothing to say where it landed, so the whole span is selected.
    expect(atPtOf(span, { pressX: 50, shownPx: 0 })).toBeNull();
  });

  test("the letter under the press, counted from the span's start", () => {
    expect(letterAt("was here", 2, { glyphs, size: 10 })).toBe(0);
    expect(letterAt("was here", 22, { glyphs, size: 10 })).toBe(4);
    expect(letterAt("was here", 400, { glyphs, size: 10 })).toBe(8);
  });

  test("a letter outside the basic plane counts as the field counts it, two units", () => {
    const script = { 𝒳: 500, " ": 500 };
    // The second 𝒳 starts 5 pt in, and 2 units into the field's text.
    expect(letterAt("𝒳𝒳 ab", 7, { glyphs: script, size: 10 })).toBe(2);
  });

  test("the word around it is selected, whichever letter of it was pressed", () => {
    expect(wordAround("was here", 4)).toEqual([4, 8]);
    expect(wordAround("was here", 1)).toEqual([0, 3]);
    expect(wordAround("was here", 8)).toEqual([4, 8]);
  });
});
