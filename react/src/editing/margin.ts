// The margin's notes, one per changed span and piece of new text, placed as a proof has them: level with
// the line they're about, and nudged down just enough that no two overlap.
import { type Editor, putBack } from "./editor";
import { newTextLineOf, takeOutNewText } from "./newText";
import type { NewTextReading, SpanReading } from "./project";

/** Where each note's middle sits, in points from the page's top, in the order its lines come, a note's height apart. */
export function notePlaces(lines: readonly { y0: number; y1: number }[], noteHeightPt: number): number[] {
  const byHeight = lines
    .map(({ y0, y1 }, index) => ({ index, wantPt: (y0 + y1) / 2 }))
    .sort((a, b) => a.wantPt - b.wantPt);
  const places = lines.map(() => 0);
  let lastPt = -Infinity;
  for (const { index, wantPt } of byHeight) {
    lastPt = Math.max(wantPt, lastPt + noteHeightPt);
    places[index] = lastPt;
  }
  return places;
}

/** The words of `original` and `now`, and how many at the start and the end are the same in both. */
function wordsKept(original: string, now: string) {
  const originalWords = [...original.matchAll(/\S+/g)];
  const nowWords = [...now.matchAll(/\S+/g)].map(([word]) => word);
  const keptAtStart = sharedCount(originalWords, nowWords, Math.min(originalWords.length, nowWords.length));
  const roomAtEnd = Math.min(originalWords.length, nowWords.length) - keptAtStart;
  const keptAtEnd = sharedCount(originalWords.toReversed(), nowWords.toReversed(), roomAtEnd);
  return { originalWords, nowWords, keptAtStart, keptAtEnd };
}

/** The words of `original` an edit to `now` changed: those left out, else the word beside what went in. */
export function changedWordsIn(original: string, now: string): string {
  const { originalWords, keptAtStart, keptAtEnd } = wordsKept(original, now);
  const changed = originalWords.slice(keptAtStart, originalWords.length - keptAtEnd);
  // Words only went in: nothing of the original changed, so name the word they went in beside.
  const first = changed[0] ?? originalWords[Math.max(keptAtStart - 1, 0)];
  // An original of no words: nothing shorter to show.
  if (first === undefined) return original;
  const last = changed.at(-1) ?? first;
  return original.slice(first.index, last.index + last[0].length);
}

/** The words an edit to `now` only added to `original`, nothing of it taken out; null when it changed any. */
export function insertedWordsIn(original: string, now: string): string | null {
  const { originalWords, nowWords, keptAtStart, keptAtEnd } = wordsKept(original, now);
  const tookOut = originalWords.length - keptAtStart - keptAtEnd > 0;
  const added = nowWords.slice(keptAtStart, nowWords.length - keptAtEnd);
  return tookOut || added.length === 0 ? null : added.join(" ");
}

/** What one change's note says: its name, for what pressing it undoes, and its words beside the page and under it. */
export interface NoteWords {
  readonly label: string;
  readonly struck: string; // the original's words it took out, struck through beside the page
  readonly inserted: string | null; // the words it only added, nothing taken out: shown with a caret instead
  readonly was: string; // what the span read, whole, or the words a redaction took, shown with `now` on hover and focus
  readonly now: string; // what the span reads now, in the list under the page
}

/**
 * The words of the note for `change`. A redaction takes its words out, so the note strikes those, only: all of the
 * line, or each run of words, an ellipsis between; an edit that only added words shows those, as a proof marks an
 * insertion, rather than striking one it kept.
 */
export function noteOf({ span, text, redacted }: SpanReading): NoteWords {
  const was = span.text;
  if (redacted === "whole") {
    return { label: `Undo: “${was}” is no longer redacted`, struck: was, inserted: null, was, now: "Redacted" };
  }
  if (redacted !== null) {
    const words = redacted.map(({ start, end }) => was.slice(start, end)).join(" … ");
    return {
      label: `Undo: “${words}” is no longer redacted`,
      struck: words,
      inserted: null,
      was: words,
      now: "Redacted",
    };
  }
  const label = `Undo: “${text}” goes back to “${was}”`;
  return { label, struck: changedWordsIn(was, text), inserted: insertedWordsIn(was, text), was, now: text };
}

/** How many of `words` from the start, up to `limit`, `now` has too, in the same places. */
function sharedCount(words: readonly RegExpExecArray[], now: readonly string[], limit: number): number {
  let count = 0;
  while (count < limit && words[count]?.[0] === now[count]) count++;
  return count;
}

/** What new text's note says: its name, for what pressing it undoes, and the words added, marked as a proof marks an insertion. */
export function newTextNoteOf({ edit }: NewTextReading): NoteWords {
  return { label: `Undo: take out “${edit.text}”`, struck: "", inserted: edit.text, was: "", now: edit.text };
}

/** One note in the margin: what it says, the line it's about, and what pressing it undoes. */
export interface MarginNote {
  readonly key: string;
  readonly note: NoteWords;
  readonly line: { readonly y0: number; readonly y1: number };
  readonly undo: (editor: Editor) => void;
}

/** A page's margin notes: each changed span's, which a press puts back, then each piece of new text's, which it takes out. */
export function marginNotesOf(changes: readonly SpanReading[], newTexts: readonly NewTextReading[]): MarginNote[] {
  const spans = changes.map((change) => ({
    key: change.span.id,
    note: noteOf(change),
    line: change.span.bbox,
    undo: (editor: Editor) => putBack(editor, change.span.id),
  }));
  const added = newTexts.map((text) => ({
    key: `new text ${text.newTextId}`,
    note: newTextNoteOf(text),
    line: newTextLineOf(text.edit),
    undo: (editor: Editor) => takeOutNewText(editor, text.newTextId),
  }));
  return [...spans, ...added];
}
