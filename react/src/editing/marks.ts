// What a span's mark shows, and what an edit in it says as typing starts: a font's warning waits for the
// edit (`editNote`), the bar's count saying it before (Rule 1). A new kind of span is a case here and a class.
import type { Copy, FitInfo, FontInfo, RedactionInfo, SpanInfo } from "../api/types";
import type { SpanReading } from "./project";
import { fill } from "./words";

/** What hover, focus or a tap says of a span, or an edit in it as typing starts. */
export interface Note {
  readonly warn: boolean;
  readonly said: string; // the server's sentence
  readonly why: string | null; // why the file's own font can't be used
}

/** Each is a line under the span, drawn by what's true of it. */
export interface Look {
  readonly formField: boolean; // a form field draws it, not the page: an edit here is left out
  readonly changed: boolean; // the user changed its words
  readonly trouble: boolean; // it went in, but not quite as typed: the server said why
  readonly redacted: boolean; // its words are to be taken out of the file
  readonly unredacted: boolean; // redacted, but the server found its words still there: the download is refused
  readonly note: Note | null; // nothing for a span that keeps its font and went in as typed
}

interface Facts {
  span: SpanInfo; // its fidelity and the face an edit here is drawn in, as the server judged it
  font: FontInfo | undefined; // for why its own copy can't be used, which the server says per font
  edited: SpanReading | undefined; // what it reads now, if an edit changed it
  fit: FitInfo | undefined; // the server's verdict on its last drawn edit
  redaction: RedactionInfo | undefined; // the server's verdict on its redaction, once drawn
  copy: Copy;
}

export function lookOf({ span, font, edited, fit, redaction, copy }: Facts): Look {
  const redacted = edited?.redacted ?? false;
  const changed = edited?.replaced ?? false;
  // A verdict counts only while the span is changed: put back, it may linger until its page is drawn again.
  const verdict = changed ? fit?.message : null;
  const looks = {
    formField: span.form_field,
    changed,
    trouble: Boolean(verdict),
    redacted,
    unredacted: redacted && redaction?.verified === false,
  };
  // Redacted: what the server found before any export, whether the words are gone and where else they were (Rule 4).
  if (redacted) return { ...looks, note: redactionNote(redaction) };
  if (verdict) return { ...looks, note: { warn: true, said: verdict, why: null } };
  // Untouched, its font's warning waits for an edit; a form field's says an edit there is left out.
  if (!changed && !span.form_field) return { ...looks, note: null };
  return { ...looks, note: fidelityNote({ span, font, copy }) };
}

/** What an edit in `span` warns of as typing starts: nothing when it keeps its look, or its font is quieted. */
export function editNote(
  span: SpanInfo,
  { font, copy, quiet }: { font: FontInfo | undefined; copy: Copy; quiet: boolean },
): Note | null {
  // A form field's is a trouble of the field's own: whatever is typed, the edit is left out.
  if (quiet || span.form_field) return null;
  return fidelityNote({ span, font, copy });
}

/** What a redaction's render found, in the server's words: nothing to say until it's drawn, or when it all went. */
function redactionNote(redaction: RedactionInfo | undefined): Note | null {
  if (redaction?.message == null) return null;
  // Its words left in, a hidden copy taken out, or every signature gone: none is what was asked, so each warns.
  return { warn: true, said: redaction.message, why: null };
}

/** Whether an edit here won't match the text around it, whatever the words: a similar font, or its own set unlike it. */
export function wontMatch(span: SpanInfo): boolean {
  return span.substitute !== null || span.why !== null;
}

/** The chip's short warning on an edit here: "a similar font", "will look different", or none when it matches. */
export function shortWarningOf(span: SpanInfo): string | null {
  if (span.substitute !== null) return "a similar font";
  return span.why === null ? null : "will look different";
}

/** Why an edit here would come out unlike the text around it, in the server's words. */
function approximateSaid(why: NonNullable<SpanInfo["why"]>, copy: Copy): string {
  return fill(copy.approximate[why.code] ?? "", why.params);
}

/** What an edit here will come to, before one is made: kept as it was, or drawn in what font. */
function fidelityNote({ span, font, copy }: Omit<Facts, "edited" | "fit" | "redaction">): Note | null {
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
