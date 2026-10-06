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

/** The words of `original` an edit to `now` changed: those left out, else the word beside what went in. */
export function changedWordsIn(original: string, now: string): string {
  const originalWords = [...original.matchAll(/\S+/g)];
  const nowWords = [...now.matchAll(/\S+/g)].map(([word]) => word);
  const keptAtStart = sharedCount(originalWords, nowWords, Math.min(originalWords.length, nowWords.length));
  const roomAtEnd = Math.min(originalWords.length, nowWords.length) - keptAtStart;
  const keptAtEnd = sharedCount(originalWords.toReversed(), nowWords.toReversed(), roomAtEnd);
  const changed = originalWords.slice(keptAtStart, originalWords.length - keptAtEnd);
  // Words only went in: nothing of the original changed, so name the word they went in beside.
  const first = changed[0] ?? originalWords[Math.max(keptAtStart - 1, 0)];
  // An original of no words: nothing shorter to show.
  if (first === undefined) return original;
  const last = changed.at(-1) ?? first;
  return original.slice(first.index, last.index + last[0].length);
}

/** How many of `words` from the start, up to `limit`, `now` has too, in the same places. */
function sharedCount(words: readonly RegExpExecArray[], now: readonly string[], limit: number): number {
  let count = 0;
  while (count < limit && words[count]?.[0] === now[count]) count++;
  return count;
}
