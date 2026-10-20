import { describe, expect, test } from "vitest";
import { aSpan, halfWideGlyphs } from "../fixtures";
import { letterAt, pressOf, selectionAt, wordAround } from "./caret";

// Every letter half the size wide: 5 pt each at 10 pt.
const glyphs = halfWideGlyphs("abcdefghijklmnopqrstuvwxyz ");
const TEN_PT = { size: 10, letter_spacing_em: 0 };

describe("the caret a press puts down", () => {
  test("a press lands as far into the span, in points, as it is into its mark", () => {
    const span = aSpan({ id: "s", bbox: { x0: 100, y0: 0, x1: 200, y1: 12 } });
    expect(pressOf(span, { pressX: 50, shownPx: 200, selects: "caret" })).toEqual({ atPt: 25, selects: "caret" });
    // Not laid out yet: nothing to say where it landed, so the whole span is selected.
    expect(pressOf(span, { pressX: 50, shownPx: 0, selects: "caret" })).toBeNull();
  });

  test("a click's caret goes in the gap nearest it; a double tap selects the word under it", () => {
    const caretAt = (atPt: number) => selectionAt("was here", { atPt, selects: "caret" }, { glyphs, span: TEN_PT });
    expect(caretAt(2)).toEqual([0, 0]);
    expect(caretAt(3)).toEqual([1, 1]);
    expect(caretAt(22)).toEqual([4, 4]);
    expect(caretAt(400)).toEqual([8, 8]);
    expect(selectionAt("was here", { atPt: 22, selects: "word" }, { glyphs, span: TEN_PT })).toEqual([4, 8]);
  });

  test("the letter under the press, counted from the span's start", () => {
    expect(letterAt("was here", 2, { glyphs, span: TEN_PT })).toBe(0);
    expect(letterAt("was here", 22, { glyphs, span: TEN_PT })).toBe(4);
    expect(letterAt("was here", 400, { glyphs, span: TEN_PT })).toBe(8);
  });

  test("a letter outside the basic plane counts as the field counts it, two units", () => {
    const script = { 𝒳: 500, " ": 500 };
    // The second 𝒳 starts 5 pt in, and 2 units into the field's text.
    expect(letterAt("𝒳𝒳 ab", 7, { glyphs: script, span: TEN_PT })).toBe(2);
  });

  test("the word around it is selected, whichever letter of it was pressed", () => {
    expect(wordAround("was here", 4)).toEqual([4, 8]);
    expect(wordAround("was here", 1)).toEqual([0, 3]);
    expect(wordAround("was here", 8)).toEqual([4, 8]);
  });
});
