import { describe, expect, test } from "vitest";
import { letterAt, wordAround } from "./caret";

// Every letter half the size wide: 5 pt each at 10 pt.
const glyphs = Object.fromEntries([..."abcdefghijklmnopqrstuvwxyz "].map((letter) => [letter, 500]));

describe("the caret a double press puts down", () => {
  test("the letter under the press, counted from the span's start", () => {
    expect(letterAt("was here", 2, glyphs, 10)).toBe(0);
    expect(letterAt("was here", 22, glyphs, 10)).toBe(4);
    expect(letterAt("was here", 400, glyphs, 10)).toBe(8);
  });

  test("the word around it is selected, whichever letter of it was pressed", () => {
    expect(wordAround("was here", 4)).toEqual([4, 8]);
    expect(wordAround("was here", 1)).toEqual([0, 3]);
    expect(wordAround("was here", 8)).toEqual([4, 8]);
  });
});
