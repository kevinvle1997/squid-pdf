import type { FitInfo, FontInfo, SpanInfo, Strategy } from "../api/types";
import { missingIn } from "./fit";
import type { SpanReading } from "./project";
import { type Drawn, landedFitOf } from "./render";
import { drawingOf } from "./substitutes";

export type Verdict = Pick<SpanInfo, "fidelity" | "why" | "substitute" | "same_widths">;

export const EXACT: Verdict = { fidelity: "exact", why: null, substitute: null, same_widths: false };

/**
 * The one verdict the note, the chip's short warning and the bar's count say: untouched, the span's own;
 * changed, or a line's run its edit moved and the render redrew, its render's once that lands (`fit`), else the one
 * worked out as it's typed (`live`).
 */
export function verdictOf(
  span: SpanInfo,
  {
    reading,
    fit,
    live,
  }: { reading: Pick<SpanReading, "replaced" | "lineEdit"> | undefined; fit: FitInfo | undefined; live: () => Verdict },
): Verdict {
  if (!isDrawnAgain(reading, fit)) return span;
  return fit ?? live();
}

/** Whether a span reading `reading` is drawn again: changed, or a line's run the render redrew (`fit`). */
export function isDrawnAgain(
  reading: Pick<SpanReading, "replaced" | "lineEdit"> | undefined,
  fit: FitInfo | undefined,
): boolean {
  return reading !== undefined && (reading.replaced || (reading.lineEdit !== undefined && fit !== undefined));
}

/**
 * How `span` reading `text` comes out, worked out as it's typed: `plan_for` and `Engine.fidelity_of` on the
 * server. Where the page's spacing hid behind a letter the copy lacks, only the render can tell, and it wins.
 */
export function liveVerdictOf(span: SpanInfo, { font, text }: { font: FontInfo | undefined; text: string }): Verdict {
  if (font === undefined) return span;
  const drawing = drawingOf(span, { font, text, missing: missingIn(text, font.glyphs) });
  if (!span.in_file || drawing.switched.length > 0) {
    const sameWidths = drawing.face === span.substitute && span.same_widths;
    return { fidelity: "substitute", why: null, substitute: drawing.face, same_widths: sameWidths };
  }
  const why = setApartBy(span, drawing.leftOut);
  return why === null ? EXACT : { ...EXACT, fidelity: "approximate", why };
}

export function editVerdictOf(
  drawn: Drawn,
  { span, font, text, strategy }: { span: SpanInfo; font: FontInfo; text: string; strategy: Strategy | undefined },
): Verdict {
  const now: SpanReading = { span, text, replaced: text !== span.text, strategy, redacted: null };
  const live = () => liveVerdictOf(span, { font, text });
  return verdictOf(span, { reading: now, fit: landedFitOf(drawn, now), live });
}

function setApartBy(span: SpanInfo, leftOut: readonly string[]): Verdict["why"] {
  const own = span.why;
  // Turned, or right to left: whatever is typed is redrawn level, left to right.
  if (own?.code === "turned_text" || own?.code === "right_to_left_text") return own;
  if (leftOut.length > 0) return { code: "undrawable_letters", params: { letters: [...leftOut] } };
  // Spaced apart, as the page sets it: a redraw closes it up. Letters its own words lack are theirs, not the edit's.
  return own?.code === "spaced_text" ? own : null;
}

// How far each verdict is from matching: a line's edit is said by its runs' furthest.
const DISTANCE: Record<Verdict["fidelity"], number> = { exact: 0, approximate: 1, substitute: 2 };

/** The furthest of `verdicts` from matching, the first where two tie: what a line's edit is said by; exact with none. */
export function worstOf(verdicts: readonly Verdict[]): Verdict {
  return verdicts.reduce<Verdict>(
    (worst, each) => (DISTANCE[each.fidelity] > DISTANCE[worst.fidelity] ? each : worst),
    EXACT,
  );
}

/** What the chip says of an edit that will match, where it says the short warning of one that won't: Find's own words. */
export const LOOKS_IDENTICAL = "looks identical";

export function shortWarningOf({ fidelity }: Pick<Verdict, "fidelity">): string | null {
  if (fidelity === "substitute") return "a similar font";
  return fidelity === "approximate" ? "will look different" : null;
}
