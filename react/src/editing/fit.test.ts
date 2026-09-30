import { describe, expect, test } from "vitest";
import type { Copy, FitRules, FontInfo, SpanInfo } from "../api/types";
import { fitOf, missingIn, optionsFor, troublesOf, widthPt } from "./fit";

// The server's thresholds, as core/constants.py sets them.
const RULES: FitRules = { tolerance_pt: 4, condense_limit: 0.05, shrink_floor: 0.9 };

// Every letter half the size wide, so a width is easy to work out: 10 letters at 10 pt is 50 pt.
const HALF = 500;
const glyphs = {
  ...Object.fromEntries([..."abcdefghijklmnopqrstuvwxyz0123456789 "].map((l) => [l, HALF])),
  ".": 300, // 3 pt at 10 pt: less than the tolerance
};
const font: FontInfo = {
  name: "Times-Roman",
  substitute: null,
  why: null,
  why_code: null,
  why_params: {},
  same_widths: true,
  glyphs,
};
const span: SpanInfo = {
  id: "s1",
  page: 0,
  text: "abcdefghij",
  font: "Times-Roman",
  size: 10,
  color: [0, 0, 0],
  bbox: { x0: 0, y0: 0, x1: 50, y1: 12 },
  origin: [0, 10],
  fidelity: "exact",
  why: null,
};
const copy = {
  missing: "no {chars} in this font, so the line is drawn in {font}",
  too_long: "{delta_pt} pt too long",
} as Copy;

describe("the fit check", () => {
  test("widths come from the server's letter widths at the span's size", () => {
    expect(widthPt("abcdefghij", glyphs, 10)).toBe(50);
  });

  test("text as long as the original fits exactly", () => {
    const fit = fitOf(span, font, "jihgfedcba", RULES);
    expect(fit).toEqual({ deltaPt: 0, missing: [], options: [] });
  });

  test("a little longer is within the tolerance: nothing to say, nothing to offer", () => {
    const fit = fitOf(span, font, "abcdefghij.", RULES);
    expect(fit.deltaPt).toBe(3);
    expect(fit.options).toEqual([]);
    expect(troublesOf(fit, RULES, copy, "Liberation Serif Regular")).toEqual([]);
  });

  test("too long names how much, in the server's sentence", () => {
    const fit = fitOf(span, font, "abcdefghijk", RULES);
    expect(troublesOf(fit, RULES, copy, "Liberation Serif Regular")).toEqual(["5.0 pt too long"]);
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
    expect(optionsFor(5, 100, RULES)).toEqual(["shrink", "condense", "as-is"]);
    // 20 pt past 100 pt: 83% is under the floor, 20% past the limit: only leave it long.
    expect(optionsFor(20, 100, RULES)).toEqual(["as-is"]);
    expect(optionsFor(3, 100, RULES)).toEqual([]);
  });
});
