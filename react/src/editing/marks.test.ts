import { describe, expect, test } from "vitest";
import { aFit, aFont, aSpan, COPY as copy } from "../fixtures";
import { editNote, lookOf, shortWarningOf } from "./marks";

const span = aSpan({ id: "s1" });
const changed = { span, text: "is s1", replaced: true, strategy: undefined, redacted: false };
const ownFont = aFont("Times-Roman");
const substitutedFont = aFont("Arial", {
  substitute: "Liberation Sans Regular",
  same_widths: false,
  why: "Not embedded.",
});
const substituted = aSpan({ id: "s2", font: "Arial", fidelity: "substitute", substitute: "Liberation Sans Regular" });

describe("what a span's mark shows, and an edit in it as typing starts", () => {
  test("a span in its own font, unedited, has nothing to say", () => {
    expect(lookOf({ span, font: ownFont, edited: undefined, fit: undefined, redaction: undefined, copy })).toEqual({
      formField: false,
      changed: false,
      trouble: false,
      redacted: false,
      unredacted: false,
      note: null,
    });
  });

  test("untouched, a span in a similar font has no mark or note: its warning waits for an edit", () => {
    const look = lookOf({
      span: substituted,
      font: substitutedFont,
      edited: undefined,
      fit: undefined,
      redaction: undefined,
      copy,
    });
    expect(look.note).toBeNull();
  });

  test("as typing starts, a similar font is said and warned, whatever its widths", () => {
    const sameWidths = { ...substituted, same_widths: true };
    expect(editNote(substituted, { font: substitutedFont, copy, quiet: false })).toEqual({
      warn: true,
      said: "Edits here use Liberation Sans Regular, which may be a different width from the original.",
      why: "Not embedded.",
    });
    expect(editNote(sameWidths, { font: substitutedFont, copy, quiet: false })).toEqual({
      warn: true,
      said: "Edits here use Liberation Sans Regular, whose letters are the same width as the original's.",
      why: "Not embedded.",
    });
    expect(shortWarningOf(substituted)).toBe("a similar font");
  });

  test("a font the user quieted says nothing in full as typing starts, but the chip's short warning stays", () => {
    expect(editNote(substituted, { font: substitutedFont, copy, quiet: true })).toBeNull();
    expect(shortWarningOf(substituted)).toBe("a similar font");
  });

  test("the span's own fidelity is what's said, not its font's first span's", () => {
    // The font's own copy draws its first span, so the font names no similar face; this span's doesn't.
    expect(editNote(substituted, { font: aFont("Arial"), copy, quiet: false })).toEqual({
      warn: true,
      said: "Edits here use Liberation Sans Regular, which may be a different width from the original.",
      why: null,
    });
    expect(editNote(span, { font: substitutedFont, copy, quiet: false })).toBeNull();
    expect(shortWarningOf(span)).toBeNull();
  });

  test("once changed, a span in a similar font says so on hover, as long as the edit stands", () => {
    const look = lookOf({
      span: substituted,
      font: substitutedFont,
      edited: changed,
      fit: undefined,
      redaction: undefined,
      copy,
    });
    expect(look.note).toMatchObject({ warn: true, why: "Not embedded." });
  });

  test("after an edit, the server's verdict comes first, and only while the span is still changed", () => {
    const fit = aFit({ message: "2.0 pt too long, so it was shrunk" });
    expect(
      lookOf({ span: substituted, font: substitutedFont, edited: changed, fit, redaction: undefined, copy }),
    ).toMatchObject({
      changed: true,
      trouble: true,
      note: { warn: true, said: "2.0 pt too long, so it was shrunk", why: null },
    });
    // Put back, before its page is drawn again: the old verdict no longer applies.
    expect(lookOf({ span, font: ownFont, edited: undefined, fit, redaction: undefined, copy })).toMatchObject({
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
      redaction: undefined,
      copy,
    });
    expect(look.formField).toBe(true);
    expect(look.note).toEqual({ warn: true, said: copy.form_field_not_edited, why: null });
  });

  test("as typing starts, text the file's own font draws but an edit won't match says why, warned", () => {
    const turned = aSpan({ id: "s3", fidelity: "approximate", why: { code: "turned_text", params: {} } });
    expect(editNote(turned, { font: ownFont, copy, quiet: false })).toEqual({
      warn: true,
      said: copy.approximate.turned_text,
      why: null,
    });
    expect(shortWarningOf(turned)).toBe("will look different");
  });

  test("a redaction says nothing until it's drawn, then what the server found, warned", () => {
    const redacted = { ...changed, replaced: false, redacted: true };
    const before = lookOf({ span, font: ownFont, edited: redacted, fit: undefined, redaction: undefined, copy });
    const signed = {
      verified: true,
      hidden_copies: ["signatures" as const],
      message: "No signatures in the download.",
    };
    const after = lookOf({ span, font: ownFont, edited: redacted, fit: undefined, redaction: signed, copy });
    expect(before).toMatchObject({ redacted: true, unredacted: false, note: null });
    expect(after).toMatchObject({ redacted: true, unredacted: false });
    expect(after.note).toEqual({ warn: true, said: "No signatures in the download.", why: null });
  });

  test("a redaction the server found still there is marked as a warning, and says so", () => {
    const redacted = { ...changed, replaced: false, redacted: true };
    const left = { verified: false, hidden_copies: [], message: "This text couldn't be removed." };
    const look = lookOf({ span, font: ownFont, edited: redacted, fit: undefined, redaction: left, copy });
    expect(look).toMatchObject({ redacted: true, unredacted: true });
    expect(look.note).toEqual({ warn: true, said: "This text couldn't be removed.", why: null });
  });
});
