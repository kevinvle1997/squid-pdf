import { describe, expect, test } from "vitest";
import shared from "../../../tests/editing/fit_cases.json";
import { aFont, aSpan, COPY as copy, RULES } from "../fixtures";
import { fitOf, missingIn, optionsFor, setAs, settingOf, troublesOf, wayDrawn, waysOffered, widthPt } from "./fit";
import { fill } from "./words";

// Every letter half the size wide, so a width is easy to work out: 10 letters at 10 pt is 50 pt.
const HALF = 500;
const glyphs = {
  ...Object.fromEntries([..."abcdefghijklmnopqrstuvwxyz0123456789 "].map((l) => [l, HALF])),
  ".": 300, // 3 pt at 10 pt: less than the tolerance
};
const font = aFont("Times-Roman", { glyphs });
const span = aSpan({ id: "s1", text: "abcdefghij", bbox: { x0: 0, y0: 0, x1: 50, y1: 12 } });

describe("a missing letter", () => {
  test("is a space too, as the server judges it, when the font has none of that kind", () => {
    // The server sends a plain space in `glyphs` wherever it draws one, a page's gap in a font with none
    // included (core/plan.py, `widths`): one not there, or a no-break space not mapped, is missing.
    expect(missingIn("a a", { a: HALF, "\u00a0": HALF })).toEqual([" "]);
    expect(missingIn("a\u00a0a", { a: HALF, " ": HALF })).toEqual(["\u00a0"]);
    expect(missingIn("a a", { a: HALF, " ": HALF })).toEqual([]);
  });
});

describe("the fit check", () => {
  test("widths come from the server's letter widths at the span's size", () => {
    expect(widthPt("abcdefghij", glyphs, 10)).toBe(50);
  });

  test("text as long as the original fits exactly", () => {
    const fit = fitOf(span, { font, text: "jihgfedcba", rules: RULES });
    expect(fit).toEqual({ originalPt: 50, deltaPt: 0, roomPt: 0, missing: [], options: [] });
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

  test("a letter the font lacks is named once", () => {
    expect(missingIn("é é ö", glyphs)).toEqual(["é", "ö"]);
  });

  test("a missing letter that draws nothing alone is named in the chip, as the server names it", () => {
    const fit = { originalPt: 50, deltaPt: 0, roomPt: 0, missing: ["\u00ad", "é"], options: [] };
    expect(troublesOf(fit, { rules: RULES, copy, substitute: "Liberation Serif Regular" })).toEqual([
      "no optional hyphen or é in this font, so the line is drawn in our Liberation Serif Regular",
    ]);
  });

  // The server's own case, test_a_letter_typed_in_two_pieces_is_the_one_the_font_has (tests/core/test_coverage.py).
  test("a letter typed in two pieces is the one the font has, as the server spells it", () => {
    const keptWhole = { ...glyphs, C: HALF, Z: HALF, é: HALF, ü: HALF };
    const inPieces = "Café in Zürich".normalize("NFD");
    expect(missingIn(inPieces, keptWhole)).toEqual([]);
    expect(
      fitOf(span, { font: aFont("Times-Roman", { glyphs: keptWhole }), text: inPieces, rules: RULES }).missing,
    ).toEqual([]);
    // Kept in pieces, a letter typed whole is drawn in pieces too.
    expect(missingIn("é", { ...glyphs, "\u0301": 0 })).toEqual([]);
    // Neither: the letter it lacks is named whole, as the faces we ship draw it.
    expect(missingIn(inPieces, glyphs)).toEqual(["C", "é", "Z", "ü"]);
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

describe("a way out", () => {
  // 5 pt past a 100 pt line: every way out is offered.
  const long = aSpan({ id: "s2", text: "abcdefghijklmnopqrst", bbox: { x0: 0, y0: 0, x1: 100, y1: 12 } });
  const fit = fitOf(long, { font, text: "abcdefghijklmnopqrstu", rules: RULES });

  test("is drawn only when it's offered; else the line is drawn as typed, as the server draws it", () => {
    expect(fit.options).toEqual(["shrink", "condense", "as-is"]);
    expect(wayDrawn("shrink", fit)).toBe("shrink");
    expect(wayDrawn(undefined, fit)).toBe("as-is");
    expect(wayDrawn("condense", { options: ["as-is"] })).toBe("as-is");
  });

  test("ends the line where the original did: smaller letters, or narrower ones", () => {
    // 105 pt set to end at 100.
    expect(setAs("shrink", fit)).toEqual({ size: 100 / 105, scaleX: 1 });
    expect(setAs("condense", fit)).toEqual({ size: 1, scaleX: 100 / 105 });
    expect(setAs("as-is", fit)).toEqual({ size: 1, scaleX: 1 });
  });

  test("is offered only where there's one to choose: leaving the line long alone is no choice", () => {
    expect(waysOffered(fit)).toEqual(["shrink", "condense", "as-is"]);
    expect(waysOffered({ options: ["as-is"] })).toEqual([]);
    expect(waysOffered({ options: [] })).toEqual([]);
  });

  test("sets the line as drawn: the way asked while it's offered, as typed if the font can't be measured", () => {
    const text = "abcdefghijklmnopqrstu";
    expect(settingOf(long, { font, text, asked: "shrink", rules: RULES })).toEqual({
      way: "shrink",
      size: 100 / 105,
      scaleX: 1,
      offered: ["shrink", "condense", "as-is"],
    });
    expect(settingOf(long, { font: undefined, text, asked: "shrink", rules: RULES })).toEqual({
      way: "as-is",
      size: 1,
      scaleX: 1,
      offered: [],
    });
  });

  test("taken, the line isn't too long any more; a missing letter still is said", () => {
    const said = { rules: RULES, copy, substitute: "Liberation Serif Regular" };
    expect(troublesOf(fit, { ...said, way: "shrink" })).toEqual([]);
    expect(troublesOf({ ...fit, missing: ["é"] }, { ...said, way: "condense" })).toEqual([
      "no é in this font, so the line is drawn in our Liberation Serif Regular",
    ]);
  });
});

// The cases the server's fit check is tested on too (tests/editing/test_fit.py): the same verdict.
describe("a line is too long only past its room, as the server says", () => {
  test.each(shared.cases)("$what", ({ original_pt, delta_pt, room_pt, too_long_by, options }) => {
    const fit = { originalPt: original_pt, deltaPt: delta_pt, roomPt: room_pt, missing: [], options: [] };
    const said = too_long_by === null ? [] : [fill(copy.too_long, { delta_pt: too_long_by })];
    expect(troublesOf(fit, { rules: shared.rules, copy, substitute: "Liberation Serif Regular" })).toEqual(said);
    expect(optionsFor(delta_pt, { originalPt: original_pt, roomPt: room_pt, rules: shared.rules })).toEqual(options);
  });
});
