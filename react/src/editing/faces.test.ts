import { describe, expect, test } from "vitest";
import { aDoc, aFont, aSpan } from "../fixtures";
import { DEFAULT_FACE, facesOf, fileOf, previewFaceOf } from "./faces";

describe("preview faces", () => {
  test("a face's file is named as the server names it", () => {
    expect(fileOf("Liberation Serif Bold")).toBe("LiberationSerif-Bold.ttf");
    expect(fileOf("Liberation Sans Bold Italic")).toBe("LiberationSans-BoldItalic.ttf");
    expect(fileOf("IBM Plex Mono Regular")).toBe("IBMPlexMono-Regular.ttf");
  });

  test("the face the server draws a span with is the one previewed: the span's own, not its font's first span's", () => {
    // Stored on one page and only named on another: the font's first span has no similar font, this one has.
    const calibri = aFont("Calibri", { look_alike: "Carlito Regular" });
    const named = aSpan({ id: "named", font: "Calibri", substitute: "Carlito Regular" });
    expect(previewFaceOf(named, { font: calibri, text: named.text })).toBe("Carlito Regular");
  });

  test("a span its file's own font draws previews in the look-alike the server falls back to", () => {
    const lato = aFont("ABCDEE+Lato-Bold", { look_alike: "Lato Bold" });
    expect(previewFaceOf(aSpan({ id: "kept", font: lato.name }), { font: lato, text: "kept" })).toBe("Lato Bold");
  });

  test("a document's faces are each once: its spans' similar fonts, its fonts' look-alikes, and the default", () => {
    const doc = aDoc({
      spans: [
        aSpan({ id: "a", font: "Arial", substitute: "Liberation Sans Regular" }),
        aSpan({ id: "b", font: "Arial", substitute: "Liberation Sans Regular" }),
      ],
      fonts: [aFont("Arial", { look_alike: "Liberation Sans Regular" }), aFont("Lato", { look_alike: "Lato Regular" })],
    });
    expect(facesOf(doc)).toEqual(new Set([DEFAULT_FACE, "Liberation Sans Regular", "Lato Regular"]));
  });
});
