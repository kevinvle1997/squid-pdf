// What a span's mark shows: its fidelity before any edit (rule 1), and the server's verdict after.
// A new kind of span (approximate, redacted) is a case here and a class in SpanMark.module.css.
import type { Copy, FitInfo, FontInfo, SpanInfo } from "../api/types";
import type { SpanReading } from "./project";
import { fill } from "./words";

/** What hover, focus or a tap says of a span. */
export interface Note {
  readonly warn: boolean;
  readonly said: string; // the server's sentence
  readonly why: string | null; // why the file's own font can't be used
}

/** Each is a line under the span, drawn by what's true of it. */
export interface Look {
  readonly substituted: boolean; // a substitute draws in place of the file's own font: "a similar font"
  readonly approximate: boolean; // the file's own font draws it, but an edit won't match: "will look different"
  readonly formField: boolean; // a form field draws it, not the page: an edit here is left out
  readonly changed: boolean; // the user changed its words
  readonly trouble: boolean; // it went in, but not quite as typed: the server said why
  readonly note: Note | null; // nothing for a span that keeps its font and went in as typed
}

interface Facts {
  span: SpanInfo; // its fidelity and the face an edit here is drawn in, as the server judged it
  font: FontInfo | undefined; // for why its own copy can't be used, which the server says per font
  edited: SpanReading | undefined; // what it reads now, if an edit changed it
  fit: FitInfo | undefined; // the server's verdict on its last drawn edit
  copy: Copy;
}

export function lookOf({ span, font, edited, fit, copy }: Facts): Look {
  const changed = edited?.replaced ?? false;
  // A verdict counts only while the span is changed: put back, it may linger until its page is drawn again.
  const verdict = changed ? fit?.message : null;
  return {
    substituted: span.fidelity === "substitute",
    approximate: span.why !== null,
    formField: span.form_field,
    changed,
    trouble: Boolean(verdict),
    note: verdict ? { warn: true, said: verdict, why: null } : fidelityNote({ span, font, copy }),
  };
}

/** Why an edit here would come out unlike the text around it, in the server's words. */
export function approximateSaid(why: NonNullable<SpanInfo["why"]>, copy: Copy): string {
  return fill(copy.approximate[why.code] ?? "", why.params);
}

/** What an edit here will come to, before one is made: kept as it was, or drawn in what font. */
function fidelityNote({ span, font, copy }: Omit<Facts, "edited" | "fit">): Note | null {
  // A form field draws it: no edit changes it yet, whatever its font.
  if (span.form_field) return { warn: true, said: copy.form_field_not_edited, why: null };
  // Its own font draws it, but an edit won't match the text around it.
  if (span.why !== null) return { warn: true, said: approximateSaid(span.why, copy), why: null };
  if (span.substitute === null) return null;
  const sentence = span.same_widths ? copy.substitute_same_widths : copy.substitute;
  // The font's reason is its first span's: it holds here only if a similar face draws that one too.
  const why = font?.substitute == null ? null : font.why;
  // Warned whatever the widths: the file's own font doesn't draw it (`decisions/product.md`, The fidelity contract).
  return { warn: true, said: fill(sentence, { font: span.substitute }), why };
}
