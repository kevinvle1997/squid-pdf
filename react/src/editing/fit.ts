// The fit check, run in the browser as the user types (typing sends no request).
// It mirrors editing/fit.py with the server's own thresholds and letter widths, so the two
// agree; where they can't (a missing letter switches the line to another font), the
// server's fit arrives with the render and wins.
import type { Copy, FitRules, FontInfo, SpanInfo, Strategy } from "../api/types";
import { MISSING_SPACE_EM } from "./constants";
import { fill } from "./words";

export interface Fit {
  deltaPt: number; // how much longer than the original, in points; negative is shorter
  missing: string[]; // letters the span's font can't draw, each once, in the order typed
  options: Strategy[]; // the ways out of an overflow, when there is one
}

const WHITESPACE = /\s/;

/** How wide `text` draws at `size`, from widths in thousandths of the size. */
export function widthPt(text: string, glyphs: Record<string, number>, size: number): number {
  let thousandths = 0;
  for (const letter of text) {
    const width = glyphs[letter];
    if (width !== undefined) thousandths += width;
    // pdfTeX's subsets have no space at all: the space is an offset, not a letter.
    else if (WHITESPACE.test(letter)) thousandths += MISSING_SPACE_EM * 1000;
  }
  return (thousandths * size) / 1000;
}

/** The letters of `text` that `glyphs` doesn't have, each once. A space is never one. */
export function missingIn(text: string, glyphs: Record<string, number>): string[] {
  const missing = new Set<string>();
  for (const letter of text) {
    if (glyphs[letter] === undefined && !WHITESPACE.test(letter)) missing.add(letter);
  }
  return [...missing];
}

/** The ways out of an overflow, in the order worth trying; none when it fits. */
export function optionsFor(deltaPt: number, originalPt: number, rules: FitRules): Strategy[] {
  if (deltaPt <= rules.tolerance_pt || originalPt <= 0) return [];
  const shrunkTo = originalPt / (originalPt + deltaPt);
  const squeezedBy = deltaPt / originalPt;
  const options: Strategy[] = [];
  if (shrunkTo >= rules.shrink_floor) options.push("shrink");
  if (squeezedBy <= rules.condense_limit) options.push("condense");
  options.push("as-is");
  return options;
}

/** What would happen if `span` read `text` instead. */
export function fitOf(span: SpanInfo, font: FontInfo, text: string, rules: FitRules): Fit {
  const originalPt = widthPt(span.text, font.glyphs, span.size);
  const deltaPt = Math.round((widthPt(text, font.glyphs, span.size) - originalPt) * 100) / 100;
  return {
    deltaPt,
    missing: missingIn(text, font.glyphs),
    options: optionsFor(deltaPt, originalPt, rules),
  };
}

/** A fit's troubles less their numbers: said again when this changes, not as the numbers tick by with each letter. */
export function troubleKindOf(fit: Fit, rules: FitRules): string {
  return `${fit.missing.join("")} ${fit.deltaPt > rules.tolerance_pt}`;
}

/** Everything that won't come out as typed, in the server's words; empty when it fits. */
export function troublesOf(fit: Fit, rules: FitRules, copy: Copy, substitute: string): string[] {
  const troubles: string[] = [];
  if (fit.missing.length > 0) troubles.push(fill(copy.missing, { chars: fit.missing, font: substitute }));
  if (fit.deltaPt > rules.tolerance_pt) troubles.push(fill(copy.too_long, { delta_pt: fit.deltaPt }));
  return troubles;
}
