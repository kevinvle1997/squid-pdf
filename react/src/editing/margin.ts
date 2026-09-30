// The margin's notes, one per changed span, placed as a proof has them: level with the line
// they're about, and nudged down just enough that no two overlap.
import type { SpanReading } from "./project";

/** Where each note's middle sits, in points from the page's top, in the order `changes` come. `gapPt` is a note's height. */
export function notePlaces(changes: readonly SpanReading[], gapPt: number): number[] {
  const byHeight = changes
    .map(({ span }, index) => ({ index, wantPt: (span.bbox.y0 + span.bbox.y1) / 2 }))
    .sort((a, b) => a.wantPt - b.wantPt);
  const places = changes.map(() => 0);
  let lastPt = -Infinity;
  for (const { index, wantPt } of byHeight) {
    lastPt = Math.max(wantPt, lastPt + gapPt);
    places[index] = lastPt;
  }
  return places;
}
