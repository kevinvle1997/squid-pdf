// How an edit comes out, or a span untouched, as the note, the chip's short warning and the bar's count all say
// it: one verdict, the server's once its render lands, worked out here as it's typed (Rule 1).
import type { FitInfo, FontInfo, SpanInfo } from "../api/types";
import { missingIn } from "./fit";
import type { SpanReading } from "./project";
import { drawingOf } from "./substitutes";

/** How an edit comes out, or the span untouched: the four fields the server sends of a span and of an edit's fit alike. */
export type Verdict = Pick<SpanInfo, "fidelity" | "why" | "substitute" | "same_widths">;

const EXACT: Verdict = { fidelity: "exact", why: null, substitute: null, same_widths: false };

/**
 * The one verdict the note, the chip's short warning and the bar's count say: untouched, the span's own;
 * changed, its render's once that lands (`fit`), else the one worked out as it's typed (`live`).
 */
export function verdictOf(
  span: SpanInfo,
  {
    reading,
    fit,
    live,
  }: { reading: Pick<SpanReading, "replaced"> | undefined; fit: FitInfo | undefined; live: () => Verdict },
): Verdict {
  if (!(reading?.replaced ?? false)) return span;
  return fit ?? live();
}

/**
 * How `span` reading `text` comes out, worked out as it's typed: `plan_for` and `Engine.fidelity_of` on the
 * server. Where the page's spacing hid behind a letter the copy lacks, only the render can tell, and it wins.
 */
export function liveVerdictOf(span: SpanInfo, { font, text }: { font: FontInfo | undefined; text: string }): Verdict {
  // Its font unknown: there's nothing to work it out from.
  if (font === undefined) return span;
  const drawing = drawingOf(span, { font, text, missing: missingIn(text, font.glyphs) });
  // The file's own copy can't be used, or lacks a letter typed that a face we ship draws: the line goes to that face.
  if (!span.in_file || drawing.switched.length > 0) {
    const sameWidths = drawing.face === span.substitute && span.same_widths;
    return { fidelity: "substitute", why: null, substitute: drawing.face, same_widths: sameWidths };
  }
  const why = setApartBy(span, drawing.leftOut);
  return why === null ? EXACT : { ...EXACT, fidelity: "approximate", why };
}

/** How an edit in the span's own font won't match the text around it, as the server checks it in turn; null when it will. */
function setApartBy(span: SpanInfo, leftOut: readonly string[]): Verdict["why"] {
  const own = span.why;
  // Turned, or right to left: whatever is typed is redrawn level, left to right.
  if (own?.code === "turned_text" || own?.code === "right_to_left_text") return own;
  if (leftOut.length > 0) return { code: "undrawable_letters", params: { letters: [...leftOut] } };
  // Spaced apart, as the page sets it: a redraw closes it up. Letters its own words lack are theirs, not the edit's.
  return own?.code === "spaced_text" ? own : null;
}

/** The chip's short warning on an edit of `verdict`: "a similar font", "will look different", or none when it matches. */
export function shortWarningOf({ fidelity }: Verdict): string | null {
  if (fidelity === "substitute") return "a similar font";
  return fidelity === "approximate" ? "will look different" : null;
}
