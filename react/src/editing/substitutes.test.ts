import { describe, expect, test } from "vitest";
import { aFont, aSpan } from "../fixtures";
import { drawingOf, draws, substituteFor } from "./substitutes";

// The server's own lists of what each face we ship draws (face-letters.json), so these are its answers.
describe("the face a line goes to", () => {
  test("is the look-alike when it draws every letter", () => {
    expect(substituteFor("Caladea Regular", "Café")).toEqual({ face: "Caladea Regular", leftOut: [] });
  });

  test("is its broadest when that leaves out fewer, as Greek in Caladea goes to Noto Serif", () => {
    expect(draws("Caladea Regular", "Ω")).toBe(false);
    expect(substituteFor("Caladea Regular", "Hi Ω")).toEqual({ face: "Noto Serif Regular", leftOut: [] });
  });

  test("stays the look-alike on a tie: a letter no face we ship draws is left out of it", () => {
    expect(substituteFor("Liberation Serif Bold", "AGREEMENT ✓")).toEqual({
      face: "Liberation Serif Bold",
      leftOut: ["✓"],
    });
  });
});

describe("a line its font can't draw whole", () => {
  const named = aFont("Times-Bold", { look_alike: "Liberation Serif Bold" });
  const inSimilar = aSpan({ id: "s", text: "AGREEMENT", substitute: "Liberation Serif Bold" });

  test("in a similar font, a letter nothing draws is left out before the edit, as the render will", () => {
    expect(drawingOf(inSimilar, { font: named, text: "AGREEMENT ✓", missing: ["✓"] })).toEqual({
      face: "Liberation Serif Bold",
      switched: [],
      leftOut: ["✓"],
    });
  });

  test("in its own font, only letters nothing draws missing: its own font keeps the line without them", () => {
    const own = aSpan({ id: "o", text: "őr", substitute: null });
    const font = aFont("Own", { look_alike: "Liberation Serif Regular" });
    expect(drawingOf(own, { font, text: "őr ✓", missing: ["✓"] })).toEqual({
      face: "Liberation Serif Regular",
      switched: [],
      leftOut: ["✓"],
    });
  });

  test("a letter the face has switches the line to it, and one nothing has is still left out", () => {
    const own = aSpan({ id: "o", text: "Hi", substitute: null });
    const font = aFont("Own", { look_alike: "Caladea Regular" });
    expect(drawingOf(own, { font, text: "Hi Ω ✓", missing: ["Ω", "✓"] })).toEqual({
      face: "Noto Serif Regular",
      switched: ["Ω"],
      leftOut: ["✓"],
    });
  });
});
