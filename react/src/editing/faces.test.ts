import { describe, expect, test } from "vitest";
import { aFont } from "../fixtures";
import { fileOf, previewFaceOf } from "./faces";

const font = (name: string, substitute: string | null = null) => aFont(name, { substitute });

describe("preview faces", () => {
  test("a face's file is named as the server names it", () => {
    expect(fileOf("Liberation Serif Bold")).toBe("LiberationSerif-Bold.ttf");
    expect(fileOf("Liberation Sans Bold Italic")).toBe("LiberationSans-BoldItalic.ttf");
    expect(fileOf("IBM Plex Mono Regular")).toBe("IBMPlexMono-Regular.ttf");
  });

  test("the face the server draws with is the one previewed", () => {
    expect(previewFaceOf(font("Calibri", "Carlito Regular"))).toBe("Carlito Regular");
  });

  test("a font kept in the file is previewed in a look-alike by its name", () => {
    expect(previewFaceOf(font("ABCDEE+Arial-BoldMT"))).toBe("Liberation Sans Bold");
    expect(previewFaceOf(font("CMR10"))).toBe("Liberation Serif Regular");
    expect(previewFaceOf(font("Courier-Oblique"))).toBe("Liberation Mono Italic");
  });
});
