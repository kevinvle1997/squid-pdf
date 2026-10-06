import { describe, expect, test } from "vitest";
import shared from "../../../tests/editing/fit_cases.json";
import { aFont, aSpan, COPY as copy, RULES } from "../fixtures";
import { fitOf, missingIn, optionsFor, troublesOf, widthPt } from "./fit";
import { fill } from "./words";

// Every letter half the size wide, so a width is easy to work out: 10 letters at 10 pt is 50 pt.
const HALF = 500;
const glyphs = {
  ...Object.fromEntries([..."abcdefghijklmnopqrstuvwxyz0123456789 "].map((l) => [l, HALF])),
  ".": 300, // 3 pt at 10 pt: less than the tolerance
};
const font = aFont("Times-Roman", { glyphs });
const span = aSpan({ id: "s1", text: "abcdefghij", bbox: { x0: 0, y0: 0, x1: 50, y1: 12 } });

describe("the fit check", () => {
  test("widths come from the server's letter widths at the span's size", () => {
    expect(widthPt("abcdefghij", glyphs, 10)).toBe(50);
  });

  test("text as long as the original fits exactly", () => {
    const fit = fitOf(span, { font, text: "jihgfedcba", rules: RULES });
    expect(fit).toEqual({ deltaPt: 0, roomPt: 0, missing: [], options: [] });
  });

  test("a little longer is within the tolerance: nothing to say, nothing to offer", () => {
    const fit = fitOf(span, { font, text: "abcdefghij.", rules: RULES });
    expect(fit.deltaPt).toBe(3);
    expect(fit.options).toEqual([]);
    expect(troublesOf(fit, { rules: RULES, copy, substitute: "Liberation Serif Regular" })).toEqual([]);
  });

  test("too long names how much, in the server's sentence", () => {
    const fit = fitOf(span, { font, text: "abcdefghijk", rules: RULES });
    expect(troublesOf(fit, { rules: RULES, copy, substitute: "Liberation Serif Regular" })).toEqual([
      "5.0 pt too long",
    ]);
  });

  test("a letter the font lacks is named once, and a space never is", () => {
    expect(missingIn("é é ö", glyphs)).toEqual(["é", "ö"]);
    expect(missingIn("a b", { a: HALF, b: HALF })).toEqual([]);
  });

  test("a space the font has no width for still takes room", () => {
    expect(widthPt("a b", { a: HALF, b: HALF }, 10)).toBe(12.5);
  });

  test("the ways out follow the server's floor and limit", () => {
    // 5 pt past 100 pt: shrinking to 95% is above the floor, squeezing 5% is at the limit.
    expect(optionsFor(5, { originalPt: 100, roomPt: 0, rules: RULES })).toEqual(["shrink", "condense", "as-is"]);
    // 20 pt past 100 pt: 83% is under the floor, 20% past the limit: only leave it long.
    expect(optionsFor(20, { originalPt: 100, roomPt: 0, rules: RULES })).toEqual(["as-is"]);
    expect(optionsFor(3, { originalPt: 100, roomPt: 0, rules: RULES })).toEqual([]);
  });
});

// The cases the server's fit check is tested on too (tests/editing/test_fit.py): the same verdict.
describe("a line is too long only past its room, as the server says", () => {
  test.each(shared.cases)("$what", ({ original_pt, delta_pt, room_pt, too_long_by, options }) => {
    const fit = { deltaPt: delta_pt, roomPt: room_pt, missing: [], options: [] };
    const said = too_long_by === null ? [] : [fill(copy.too_long, { delta_pt: too_long_by })];
    expect(troublesOf(fit, { rules: shared.rules, copy, substitute: "Liberation Serif Regular" })).toEqual(said);
    expect(optionsFor(delta_pt, { originalPt: original_pt, roomPt: room_pt, rules: shared.rules })).toEqual(options);
  });
});
