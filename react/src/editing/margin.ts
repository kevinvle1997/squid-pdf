// The margin's notes, one per changed span and piece of new text, placed as a proof has them: level with
// the line they're about, and nudged down just enough that no two overlap.
import { NOTE_WORDS_AROUND } from "./constants";
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

/** Whether a note's words were cut from a longer line, before them and after: an ellipsis says so. */
export interface Cut {
  readonly before: boolean;
  readonly after: boolean;
}

/** Words shown whole, cut from nothing. */
export const UNCUT: Cut = { before: false, after: false };

/**
 * What `original` and `now` read around an edit between them: the words it changed and `NOTE_WORDS_AROUND`
 * either side, whole, from the same word in both, and where the line goes on past them.
 */
export function excerptsOf(original: string, now: string): { was: string; now: string; cut: Cut } {
  const { originalWords, nowWords, keptAtStart, keptAtEnd } = wordsKept(original, now);
  const from = Math.max(keptAtStart - NOTE_WORDS_AROUND, 0);
  const wasTo = Math.min(originalWords.length - keptAtEnd + NOTE_WORDS_AROUND, originalWords.length);
  const nowTo = Math.min(nowWords.length - keptAtEnd + NOTE_WORDS_AROUND, nowWords.length);
  // Both are cut at the same kept words, so where one goes on the other does too.
  const cut = { before: from > 0, after: wasTo < originalWords.length };
  return { was: wordsBetween(original, { from, to: wasTo }), now: wordsBetween(now, { from, to: nowTo }), cut };
}

/** `text` from its word `from` up to its word `to`, its own spacing kept; all of it with no words to cut at. */
function wordsBetween(text: string, { from, to }: { from: number; to: number }): string {
  const words = [...text.matchAll(/\S+/g)];
  const first = words[from];
  const last = words[to - 1];
  if (first === undefined || last === undefined) return text;
  return text.slice(first.index, last.index + last[0].length);
}

/** `words` as a note says them: an ellipsis on each side they were cut from the line. */
export function inLine(words: string, cut: Cut): string {
  return `${cut.before ? "… " : ""}${words}${cut.after ? " …" : ""}`;
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
  // The original's words it took out, struck through beside the page: a run each, an ellipsis between left unstruck.
  readonly struck: readonly string[];
  readonly inserted: string | null; // the words it only added, nothing taken out: shown with a caret instead
  // What the span read around the change, or each run of words a redaction took: struck in the list under the page,
  // and shown over `now` on hover and focus. None for new text.
  readonly was: readonly string[];
  readonly now: string; // what the span reads now around the change, in the list under the page
  readonly cut: Cut; // whether `was` and `now` were cut from a longer line
}

/** Runs of words as one line, an ellipsis between, as a note's name and its hover say them. */
export function joinedRuns(runs: readonly string[]): string {
  return runs.join(" … ");
}

/**
 * The words of the note for `change`. A redaction takes its words out, so the note strikes those, only: all of the
 * line, or each run of words; an edit that only added words shows those, as a proof marks an insertion, rather than
 * striking one it kept.
 */
export function noteOf({ span, text, redacted }: SpanReading): NoteWords {
  if (redacted !== null) {
    const runs = redacted === "whole" ? [span.text] : redacted.map(({ start, end }) => span.text.slice(start, end));
    const label = `Undo: “${joinedRuns(runs)}” is no longer redacted`;
    return { label, struck: runs, inserted: null, was: runs, now: "Redacted", cut: UNCUT };
  }
  const original = span.text;
  // A screen reader hears the whole line: the note's name is all it has to go on.
  const label = `Undo: “${text}” goes back to “${original}”`;
  const { was, now, cut } = excerptsOf(original, text);
  const struck = [changedWordsIn(original, text)];
  return { label, struck, inserted: insertedWordsIn(original, text), was: [was], now, cut };
}

/** How many of `words` from the start, up to `limit`, `now` has too, in the same places. */
function sharedCount(words: readonly RegExpExecArray[], now: readonly string[], limit: number): number {
  let count = 0;
  while (count < limit && words[count]?.[0] === now[count]) count++;
  return count;
}

/** What new text's note says: its name, for what pressing it undoes, and the words added, marked as a proof marks an insertion. */
export function newTextNoteOf({ edit }: NewTextReading): NoteWords {
  const label = `Undo: take out “${edit.text}”`;
  return { label, struck: [], inserted: edit.text, was: [], now: edit.text, cut: UNCUT };
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
