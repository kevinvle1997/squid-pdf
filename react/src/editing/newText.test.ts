import { describe, expect, test } from "vitest";
import { aDoc, aFit, aFont, anInsert, aSpan, halfWideGlyphs } from "../fixtures";
import { layoutOf } from "./editor";
import { shownTextsAmong } from "./newText";

// Arial is only named in the file: a similar font draws new text in it. Caveat Bold, a face we ship, draws its own.
const ARIAL = aSpan({
  id: "arial",
  font: "Arial",
  fidelity: "substitute",
  substitute: "Liberation Sans Regular",
  in_file: false,
});
const LAYOUT = layoutOf(
  aDoc({
    spans: [ARIAL],
    fonts: [aFont("Arial", { substitute: "Liberation Sans Regular", glyphs: halfWideGlyphs("ab") })],
  }),
);
const inArial = { id: 1, textId: 1, edit: anInsert({ font: "Arial", text: "ab" }) };
const inCaveat = { id: 2, textId: 2, edit: anInsert({ font: "Caveat Bold", text: "ab" }) };

describe("the new text a shown row of the bar's list marks", () => {
  test("is the new text in that row's font, not text its own face draws, and none while no row is shown", () => {
    const facts = { layout: LAYOUT, fits: {} };
    expect(shownTextsAmong([inArial, inCaveat], { ...facts, shown: new Set(["font:Arial"]) })).toEqual([inArial]);
    expect(shownTextsAmong([inArial, inCaveat], { ...facts, shown: new Set() })).toEqual([]);
  });

  test("goes by its render once that version landed", () => {
    const fits = { 1: aFit({ fidelity: "exact" }) };
    expect(shownTextsAmong([inArial], { layout: LAYOUT, fits, shown: new Set(["font:Arial"]) })).toEqual([]);
  });
});
