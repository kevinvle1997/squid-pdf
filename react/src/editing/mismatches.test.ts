import { describe, expect, test } from "vitest";
import type { SpanInfo } from "../api/types";
import { aFit, aFont, aSpan, halfWideGlyphs, SENTENCES } from "../fixtures";
import { mismatchesOf, placeOf, shownAmong } from "./mismatches";

const NOT_IN_FILE = "This font isn't stored in the file, so a similar font stands in for it.";
const fonts = new Map([
  ["Arial", aFont("Arial", { substitute: "Liberation Sans", why: NOT_IN_FILE })],
  ["ABCDEF+Arial", aFont("ABCDEF+Arial", { shown_name: "Arial", substitute: "Liberation Sans", why: NOT_IN_FILE })],
  ["Times-Roman", aFont("Times-Roman")],
]);
/** A place before any change: the span, judged on its own words. */
const atRest = (span: SpanInfo) => ({ span, verdict: span, missing: [] });
const similar = (id: string, font = "Arial") =>
  atRest(aSpan({ id, font, fidelity: "substitute", substitute: "Liberation Sans", in_file: false }));
const turned = (id: string) => atRest(aSpan({ id, fidelity: "approximate", why: { code: "turned_text", params: {} } }));
const undrawable = (id: string, letters: string[]) =>
  atRest(aSpan({ id, fidelity: "approximate", why: { code: "undrawable_letters", params: { letters } } }));

describe("where an edit won't match, grouped", () => {
  test("a similar font's places are under the font as shown, its copies together, with the server's why", () => {
    const mismatches = mismatchesOf([similar("a"), similar("b", "ABCDEF+Arial"), turned("c")], {
      fonts,
      sentences: SENTENCES,
    });
    expect(mismatches.count).toBe(3);
    expect(mismatches.fonts).toEqual([{ key: "font:Arial", name: "Arial", places: 2, sentence: NOT_IN_FILE }]);
  });

  test("the rest are under why the file's own won't match, the letters of every place named once, most places first", () => {
    const places = [undrawable("a", ["中"]), turned("b"), undrawable("c", ["中", "文"]), undrawable("d", [])];
    const mismatches = mismatchesOf(places, { fonts, sentences: SENTENCES });
    expect(mismatches.fonts).toEqual([]);
    expect(mismatches.reasons).toEqual([
      {
        key: "reason:undrawable_letters",
        code: "undrawable_letters",
        places: 3,
        sentence: "No font we have can draw 中 文 here, so an edit here leaves them out.",
      },
      { key: "reason:turned_text", code: "turned_text", places: 1, sentence: SENTENCES.approximate.turned_text },
    ]);
  });

  test("a change its own font lacks letters for is under its font, saying which", () => {
    const span = aSpan({ id: "a", fidelity: "approximate", why: { code: "turned_text", params: {} } });
    const verdict = aFit({ fidelity: "substitute", substitute: "Liberation Serif Regular" });
    const mismatches = mismatchesOf([{ span, verdict, missing: ["é", "ñ"] }], { fonts, sentences: SENTENCES });
    expect(mismatches.reasons).toEqual([]);
    expect(mismatches.fonts).toEqual([
      {
        key: "font:Times-Roman",
        name: "Times-Roman",
        places: 1,
        sentence: "no é or ñ in this font, so the line is drawn in our Liberation Serif Regular",
      },
    ]);
  });
});

describe("the places a shown row marks on a page", () => {
  const arial = similar("a").span;
  const ownFont = aSpan({ id: "own" });
  const spans = [arial, similar("b").span, ownFont, turned("c").span];
  const facts = { readings: new Map(), fits: {}, fonts };

  test("before any change, every place in that row: the font's, not another row's, nor text that matches", () => {
    const shown = shownAmong(spans, { ...facts, shown: new Set(["font:Arial"]), changed: false });
    expect(shown.map((span) => span.id)).toEqual(["a", "b"]);
    expect(shownAmong(spans, { ...facts, shown: new Set(), changed: false })).toEqual([]);
  });

  test("after a change, only the changes in it, as the list then counts them", () => {
    const readings = new Map([
      ["a", { span: arial, text: "is a", replaced: true, strategy: undefined, redacted: null }],
    ]);
    const shown = shownAmong(spans, { ...facts, readings, shown: new Set(["font:Arial"]), changed: true });
    expect(shown.map((span) => span.id)).toEqual(["a"]);
  });
});

describe("a changed span, as the bar counts it", () => {
  // Its copy of the font lacks "€", so judged on its own words it's a similar font's; the copy draws every other letter.
  const lacking = aSpan({
    id: "l",
    text: "Total 14 €",
    fidelity: "substitute",
    substitute: "Liberation Serif Regular",
  });
  const font = aFont("Times-Roman", { glyphs: halfWideGlyphs("Total 0123456789") });
  const typed = (span: SpanInfo, text: string) => ({
    span,
    text,
    replaced: true,
    strategy: undefined,
    redacted: null,
  });

  test("drawn in its own font, as its render says, a span a similar font drew at upload is no place", () => {
    const reading = typed(lacking, "Total 2");
    expect(placeOf(lacking, { changed: true, reading, fit: aFit(), font })).toBeUndefined();
    // Before its render lands, as worked out from the words typed.
    expect(placeOf(lacking, { changed: true, reading, fit: undefined, font })).toBeUndefined();
  });

  test("an edit its render says a similar font draws is a place, under its font", () => {
    const span = aSpan({ id: "own", text: "Total 14" });
    const fit = aFit({ fidelity: "substitute", substitute: "Liberation Serif Regular" });
    const place = placeOf(span, { changed: true, reading: typed(span, "Total 2"), fit, font });
    expect(place).toEqual({ span, verdict: fit, missing: [] });
    const mismatches = mismatchesOf(place === undefined ? [] : [place], { fonts, sentences: SENTENCES });
    expect(mismatches.fonts.map((row) => [row.key, row.places])).toEqual([["font:Times-Roman", 1]]);
  });

  test("an edit with a letter no font we have draws is under that reason, though the span matched before", () => {
    const span = aSpan({ id: "own", text: "Total 14" });
    const place = placeOf(span, { changed: true, reading: typed(span, "Total 中"), fit: undefined, font });
    const mismatches = mismatchesOf(place === undefined ? [] : [place], { fonts, sentences: SENTENCES });
    expect(mismatches.reasons).toEqual([
      {
        key: "reason:undrawable_letters",
        code: "undrawable_letters",
        places: 1,
        sentence: "No font we have can draw 中 here, so an edit here leaves them out.",
      },
    ]);
  });
});
