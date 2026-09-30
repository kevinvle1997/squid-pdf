import { describe, expect, test } from "vitest";
import { aFit, aFont, aSpan, COPY as copy } from "../fixtures";
import { lookOf } from "./marks";

const span = aSpan({ id: "s1" });
const changed = { span, text: "is s1", replaced: true, strategy: undefined, redacted: false };
const ownFont = aFont("Times-Roman");
const standIn = aFont("Arial", { substitute: "Liberation Sans Regular", same_widths: false, why: "Not embedded." });

describe("what a span's mark shows", () => {
  test("a span in its own font, unedited, has nothing to say", () => {
    expect(lookOf({ font: ownFont, edited: undefined, fit: undefined, copy })).toEqual({
      similar: false,
      changed: false,
      trouble: false,
      note: null,
    });
  });

  test("before any edit, a stand-in font is said, and warned when its widths differ", () => {
    const look = lookOf({ font: standIn, edited: undefined, fit: undefined, copy });
    expect(look.similar).toBe(true);
    expect(look.note).toEqual({
      warn: true,
      said: "Edits here use Liberation Sans Regular, which may be a different width from the original.",
      why: "Not embedded.",
    });
  });

  test("after an edit, the server's verdict comes first, and only while the span is still changed", () => {
    const fit = aFit({ message: "2.0 pt too long, so it was shrunk" });
    expect(lookOf({ font: standIn, edited: changed, fit, copy })).toMatchObject({
      changed: true,
      trouble: true,
      note: { warn: true, said: "2.0 pt too long, so it was shrunk", why: null },
    });
    // Put back, before its page is drawn again: the old verdict no longer applies.
    expect(lookOf({ font: ownFont, edited: undefined, fit, copy })).toMatchObject({ trouble: false, note: null });
  });
});
