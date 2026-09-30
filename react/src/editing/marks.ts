// What a span's mark shows: its fidelity before any edit (rule 1), and the server's verdict after.
// A new kind of span (approximate, redacted) is a case here and a class in SpanMark.module.css.
import type { Copy, FitInfo, FontInfo } from "../api/types";
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
  readonly similar: boolean; // a similar font stands in for the file's own
  readonly changed: boolean; // the user changed its words
  readonly trouble: boolean; // it went in, but not quite as typed: the server said why
  readonly note: Note | null; // nothing for a span that keeps its font and went in as typed
}

interface Facts {
  font: FontInfo | undefined;
  edited: SpanReading | undefined; // what it reads now, if an edit changed it
  fit: FitInfo | undefined; // the server's verdict on its last drawn edit
  copy: Copy;
}

export function lookOf({ font, edited, fit, copy }: Facts): Look {
  const changed = edited?.replaced ?? false;
  // A verdict counts only while the span is changed: put back, it may linger until its page is drawn again.
  const verdict = changed ? fit?.message : null;
  return {
    similar: font?.substitute != null,
    changed,
    trouble: Boolean(verdict),
    note: verdict ? { warn: true, said: verdict, why: null } : fidelityNote(font, copy),
  };
}

/** What a span's font means for an edit, before one is made. */
function fidelityNote(font: FontInfo | undefined, copy: Copy): Note | null {
  if (font?.substitute == null) return null;
  const sentence = font.same_widths ? copy.stand_in_same_widths : copy.stand_in;
  return { warn: !font.same_widths, said: fill(sentence, { font: font.substitute }), why: font.why };
}
