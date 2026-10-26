import { describe, expect, test } from "vitest";
import { aFont, aSpan } from "../fixtures";
import { fontOf, fontsByObject, shownNameIn } from "./spanFonts";

describe("the font that draws a span", () => {
  // Two fonts listed under one name, as a file merged from two leaves them: each its own letters.
  const FONTS = fontsByObject([
    aFont("Calibri", { object: 5, glyphs: { a: 479 } }),
    aFont("Calibri", { object: 9, glyphs: { b: 525 } }),
    aFont("Helvetica", { substitute: "Liberation Sans Regular" }), // no object: no page lists it
  ]);

  test("is the one its object names, where two fonts share a name", () => {
    const span = aSpan({ id: "s", font: "Calibri", font_object: 9 });
    expect(fontOf(FONTS, span)?.glyphs).toEqual({ b: 525 });
    expect(shownNameIn(FONTS, span)).toBe("Calibri");
  });

  test("goes by its name where no page lists it, and by its own name where the server lists none", () => {
    expect(fontOf(FONTS, aSpan({ id: "field", font: "Helvetica" }))?.substitute).toBe("Liberation Sans Regular");
    expect(fontOf(FONTS, aSpan({ id: "new", font: "Lato", font_object: 12 }))).toBeUndefined();
    expect(shownNameIn(FONTS, aSpan({ id: "new", font: "Lato", font_object: 12 }))).toBe("Lato");
  });
});
