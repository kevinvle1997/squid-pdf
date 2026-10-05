// The fit check, run in the browser as the user types (typing sends no request).
// It mirrors editing/fit.py with the server's own thresholds and letter widths, so the two
// agree; where they can't (a missing letter switches the line to another font), the
// server's fit arrives with the render and wins.
import type { Copy, FitRules, FontInfo, SpanInfo, Strategy } from "../api/types";
import { MISSING_SPACE_EM } from "./constants";
import { fill } from "./words";

export interface Fit {
  deltaPt: number; // how much longer than the original, in points; negative is shorter
  roomPt: number; // the free space after the span on its line: too long is only past it
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

/** Whether a line `deltaPt` longer runs past its room, or the tolerance if wider: `runs_past` in editing/fit.py. */
export function runsPast(deltaPt: number, roomPt: number, rules: FitRules): boolean {
  return deltaPt > Math.max(rules.tolerance_pt, roomPt + rules.room_slack_pt);
}

/** The ways out of an overflow, in the order worth trying; none when it fits in its room. Each ends where the original did. */
export function optionsFor(
  deltaPt: number,
  { originalPt, roomPt, rules }: { originalPt: number; roomPt: number; rules: FitRules },
): Strategy[] {
  if (!runsPast(deltaPt, roomPt, rules) || originalPt <= 0) return [];
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
  const roomPt = span.room_pt;
  return {
    deltaPt,
    roomPt,
    missing: missingIn(text, font.glyphs),
    options: optionsFor(deltaPt, { originalPt, roomPt, rules }),
  };
}

/** A fit's troubles less their numbers: said again when this changes, not as the numbers tick by with each letter. */
export function troubleKindOf(fit: Fit, rules: FitRules): string {
  return `${fit.missing.join("")} ${runsPast(fit.deltaPt, fit.roomPt, rules)}`;
}

/** Everything that won't come out as typed, in the server's words; empty when it fits. */
export function troublesOf(fit: Fit, rules: FitRules, copy: Copy, substitute: string): string[] {
  const troubles: string[] = [];
  if (fit.missing.length > 0) troubles.push(fill(copy.missing, { chars: fit.missing, font: substitute }));
  // The part past its room is the part that collides.
  if (runsPast(fit.deltaPt, fit.roomPt, rules)) {
    const pastRoomPt = Math.round((fit.deltaPt - fit.roomPt) * 100) / 100;
    troubles.push(fill(copy.too_long, { delta_pt: pastRoomPt }));
  }
  return troubles;
}
