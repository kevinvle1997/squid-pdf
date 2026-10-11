import { describe, expect, test } from "vitest";
import { aFit, aFont, aSpan, COPY as copy } from "../fixtures";
import { lookOf } from "./marks";

const span = aSpan({ id: "s1" });
const changed = { span, text: "is s1", replaced: true, strategy: undefined, redacted: false };
const ownFont = aFont("Times-Roman");
const substitutedFont = aFont("Arial", {
  substitute: "Liberation Sans Regular",
  same_widths: false,
  why: "Not embedded.",
});
const substituted = aSpan({ id: "s2", font: "Arial", fidelity: "substitute", substitute: "Liberation Sans Regular" });

describe("what a span's mark shows", () => {
  test("a span in its own font, unedited, has nothing to say", () => {
    expect(lookOf({ span, font: ownFont, edited: undefined, fit: undefined, copy })).toEqual({
      substituted: false,
      approximate: false,
      formField: false,
      changed: false,
      trouble: false,
      note: null,
    });
  });

  test("before any edit, a similar font is said, and warned when its widths differ", () => {
    const look = lookOf({ span: substituted, font: substitutedFont, edited: undefined, fit: undefined, copy });
    expect(look.substituted).toBe(true);
    expect(look.note).toEqual({
      warn: true,
      said: "Edits here use Liberation Sans Regular, which may be a different width from the original.",
      why: "Not embedded.",
    });
  });

  test("the span's own fidelity is what's said, not its font's first span's", () => {
    // The font's own copy draws its first span, so the font names no similar face; this span's doesn't.
    const look = lookOf({ span: substituted, font: aFont("Arial"), edited: undefined, fit: undefined, copy });
    const exactHere = lookOf({ span, font: substitutedFont, edited: undefined, fit: undefined, copy });
    expect(look.substituted).toBe(true);
    expect(look.note).toEqual({
      warn: true,
      said: "Edits here use Liberation Sans Regular, which may be a different width from the original.",
      why: null,
    });
    expect(exactHere.substituted).toBe(false);
    expect(exactHere.note).toBeNull();
  });

  test("after an edit, the server's verdict comes first, and only while the span is still changed", () => {
    const fit = aFit({ message: "2.0 pt too long, so it was shrunk" });
    expect(lookOf({ span: substituted, font: substitutedFont, edited: changed, fit, copy })).toMatchObject({
      changed: true,
      trouble: true,
      note: { warn: true, said: "2.0 pt too long, so it was shrunk", why: null },
    });
    // Put back, before its page is drawn again: the old verdict no longer applies.
    expect(lookOf({ span, font: ownFont, edited: undefined, fit, copy })).toMatchObject({
      trouble: false,
      note: null,
    });
  });

  test("before any edit, text a form field draws says an edit here is left out, whatever its font", () => {
    const look = lookOf({
      span: { ...substituted, form_field: true },
      font: substitutedFont,
      edited: undefined,
      fit: undefined,
      copy,
    });
    expect(look.formField).toBe(true);
    expect(look.note).toEqual({ warn: true, said: copy.form_field_not_edited, why: null });
  });

  test("before any edit, text the file's own font draws but an edit won't match says why, warned", () => {
    const why = { code: "turned_text" as const, params: {} };
    const look = lookOf({
      span: { ...span, fidelity: "approximate", why },
      font: ownFont,
      edited: undefined,
      fit: undefined,
      copy,
    });
    expect(look.approximate).toBe(true);
    expect(look.note).toEqual({ warn: true, said: copy.approximate.turned_text, why: null });
  });
});
