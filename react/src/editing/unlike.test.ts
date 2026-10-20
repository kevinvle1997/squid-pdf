import { describe, expect, test } from "vitest";
import { aFont, aSpan, COPY } from "../fixtures";
import { shownAmong, unlikeOf } from "./unlike";

const NOT_IN_FILE = "This font isn't stored in the file, so a similar font stands in for it.";
const fonts = new Map([
  ["Arial", aFont("Arial", { substitute: "Liberation Sans", why: NOT_IN_FILE })],
  ["ABCDEF+Arial", aFont("ABCDEF+Arial", { shown_name: "Arial", substitute: "Liberation Sans", why: NOT_IN_FILE })],
  ["Times-Roman", aFont("Times-Roman")],
]);
const similar = (id: string, font = "Arial") => ({
  span: aSpan({ id, font, substitute: "Liberation Sans" }),
  missing: [],
});
const turned = (id: string) => ({
  span: aSpan({ id, why: { code: "turned_text", params: {} } }),
  missing: [],
});
const undrawable = (id: string, letters: string[]) => ({
  span: aSpan({ id, why: { code: "undrawable_letters", params: { letters } } }),
  missing: [],
});

describe("where an edit won't match, grouped", () => {
  test("a similar font's places are under the font as shown, its copies together, with the server's why", () => {
    const unlike = unlikeOf([similar("a"), similar("b", "ABCDEF+Arial"), turned("c")], { fonts, copy: COPY });
    expect(unlike.count).toBe(3);
    expect(unlike.fonts).toEqual([{ key: "font:Arial", first: "a", name: "Arial", places: 2, said: NOT_IN_FILE }]);
  });

  test("the rest are under why the file's own won't match, the letters of every place said once, most places first", () => {
    const places = [undrawable("a", ["中"]), turned("b"), undrawable("c", ["中", "文"]), undrawable("d", [])];
    const unlike = unlikeOf(places, { fonts, copy: COPY });
    expect(unlike.fonts).toEqual([]);
    expect(unlike.reasons).toEqual([
      {
        key: "reason:undrawable_letters",
        first: "a",
        code: "undrawable_letters",
        places: 3,
        said: "No font we have can draw 中 文 here, so an edit here leaves them out.",
      },
      { key: "reason:turned_text", first: "b", code: "turned_text", places: 1, said: COPY.approximate.turned_text },
    ]);
  });

  test("a change its own font lacks letters for is under its font, saying which", () => {
    const span = aSpan({ id: "a", why: { code: "turned_text", params: {} } });
    const unlike = unlikeOf([{ span, missing: ["é", "ñ"] }], { fonts, copy: COPY });
    expect(unlike.reasons).toEqual([]);
    expect(unlike.fonts).toEqual([
      {
        key: "font:Times-Roman",
        first: "a",
        name: "Times-Roman",
        places: 1,
        said: "no é or ñ in this font, so the line is drawn in our Liberation Serif Regular",
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
      ["a", { span: arial, text: "is a", replaced: true, strategy: undefined, redacted: false }],
    ]);
    const shown = shownAmong(spans, { ...facts, readings, shown: new Set(["font:Arial"]), changed: true });
    expect(shown.map((span) => span.id)).toEqual(["a"]);
  });
});
