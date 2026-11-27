import { NOTE_WORDS_AROUND } from "./constants";
import { type Editor, putBack } from "./editor";
import type { Line } from "./lines";
import { newTextLineOf, takeOutNewText } from "./newText";
import type { NewTextReading, SpanReading } from "./project";

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

function wordsKept(original: string, now: string) {
  const originalWords = [...original.matchAll(/\S+/g)];
  const nowWords = [...now.matchAll(/\S+/g)].map(([word]) => word);
  const keptAtStart = sharedCount(originalWords, nowWords, Math.min(originalWords.length, nowWords.length));
  const roomAtEnd = Math.min(originalWords.length, nowWords.length) - keptAtStart;
  const keptAtEnd = sharedCount(originalWords.toReversed(), nowWords.toReversed(), roomAtEnd);
  return { originalWords, nowWords, keptAtStart, keptAtEnd };
}

export function changedWordsIn(original: string, now: string): string {
  const { originalWords, keptAtStart, keptAtEnd } = wordsKept(original, now);
  const changed = originalWords.slice(keptAtStart, originalWords.length - keptAtEnd);
  const first = changed[0] ?? originalWords[Math.max(keptAtStart - 1, 0)];
  if (first === undefined) return original;
  const last = changed.at(-1) ?? first;
  return original.slice(first.index, last.index + last[0].length);
}

export interface Cut {
  readonly before: boolean;
  readonly after: boolean;
}

export const UNCUT: Cut = { before: false, after: false };

export function excerptsOf(original: string, now: string): { was: string; now: string; cut: Cut } {
  const { originalWords, nowWords, keptAtStart, keptAtEnd } = wordsKept(original, now);
  const from = Math.max(keptAtStart - NOTE_WORDS_AROUND, 0);
  const wasTo = Math.min(originalWords.length - keptAtEnd + NOTE_WORDS_AROUND, originalWords.length);
  const nowTo = Math.min(nowWords.length - keptAtEnd + NOTE_WORDS_AROUND, nowWords.length);
  const cut = { before: from > 0, after: wasTo < originalWords.length };
  return { was: wordsBetween(original, { from, to: wasTo }), now: wordsBetween(now, { from, to: nowTo }), cut };
}

function wordsBetween(text: string, { from, to }: { from: number; to: number }): string {
  const words = [...text.matchAll(/\S+/g)];
  const first = words[from];
  const last = words[to - 1];
  if (first === undefined || last === undefined) return text;
  return text.slice(first.index, last.index + last[0].length);
}

export function inLine(words: string, cut: Cut): string {
  return `${cut.before ? "… " : ""}${words}${cut.after ? " …" : ""}`;
}

export function insertedWordsIn(original: string, now: string): string | null {
  const { originalWords, nowWords, keptAtStart, keptAtEnd } = wordsKept(original, now);
  const tookOut = originalWords.length - keptAtStart - keptAtEnd > 0;
  const added = nowWords.slice(keptAtStart, nowWords.length - keptAtEnd);
  return tookOut || added.length === 0 ? null : added.join(" ");
}

export interface NoteWords {
  readonly label: string;
  readonly struck: readonly string[];
  readonly inserted: string | null;
  readonly was: readonly string[];
  readonly now: string;
  readonly cut: Cut;
}

export function joinedRuns(runs: readonly string[]): string {
  return runs.join(" … ");
}

export function noteOf({ span, text, redacted }: SpanReading): NoteWords {
  if (redacted !== null) {
    const runs = redacted === "whole" ? [span.text] : redacted.map(({ start, end }) => span.text.slice(start, end));
    const label = `Undo: “${joinedRuns(runs)}” is no longer redacted`;
    return { label, struck: runs, inserted: null, was: runs, now: "Redacted", cut: UNCUT };
  }
  const original = span.text;
  const label = `Undo: “${text}” goes back to “${original}”`;
  const { was, now, cut } = excerptsOf(original, text);
  const struck = [changedWordsIn(original, text)];
  return { label, struck, inserted: insertedWordsIn(original, text), was: [was], now, cut };
}

function sharedCount(words: readonly RegExpExecArray[], now: readonly string[], limit: number): number {
  let count = 0;
  while (count < limit && words[count]?.[0] === now[count]) count++;
  return count;
}

export function newTextNoteOf({ edit }: NewTextReading): NoteWords {
  const label = `Undo: take out “${edit.text}”`;
  return { label, struck: [], inserted: edit.text, was: [], now: edit.text, cut: UNCUT };
}

export interface MarginNote {
  readonly key: string;
  readonly note: NoteWords;
  readonly line: { readonly y0: number; readonly y1: number };
  readonly undo: (editor: Editor) => void;
}

function lineNoteOf(line: Line, changes: readonly SpanReading[]): NoteWords | null {
  const redacted = changes.filter((change) => change.redacted !== null).map((change) => noteOf(change));
  if (redacted.length > 0) {
    const runs = redacted.flatMap((note) => note.was);
    const label = `Undo: “${joinedRuns(runs)}” is no longer redacted`;
    return { label, struck: runs, inserted: null, was: runs, now: "Redacted", cut: UNCUT };
  }
  if (!changes.some((change) => change.replaced)) return null;
  const readings = new Map(changes.map((change) => [change.span.id, change.text]));
  const original = line.spans.map((span) => span.text).join("");
  const now = line.spans.map((span) => readings.get(span.id) ?? span.text).join("");
  const [first] = line.spans;
  if (first === undefined) return null;
  const whole = { span: { ...first, text: original }, text: now, replaced: true, strategy: undefined, redacted: null };
  return noteOf(whole);
}

function changeNotesOf(changes: readonly SpanReading[], lines: ReadonlyMap<string, Line>): MarginNote[] {
  const groups = new Map<string, SpanReading[]>();
  for (const change of changes) {
    const key = lines.get(change.span.id)?.id ?? change.span.id;
    groups.set(key, [...(groups.get(key) ?? []), change]);
  }
  return [...groups].flatMap(([key, group]) => {
    const [first] = group;
    const line = lines.get(key);
    const note = line === undefined ? first && noteOf(first) : lineNoteOf(line, group);
    const box = line?.bbox ?? first?.span.bbox;
    if (note === null || note === undefined || box === undefined) return [];
    return [{ key, note, line: box, undo: (editor: Editor) => putBack(editor, key) }];
  });
}

export function marginNotesOf(
  changes: readonly SpanReading[],
  newTexts: readonly NewTextReading[],
  lines: ReadonlyMap<string, Line>,
): MarginNote[] {
  const spans = changeNotesOf(changes, lines);
  const added = newTexts.map((text) => ({
    key: `new text ${text.newTextId}`,
    note: newTextNoteOf(text),
    line: newTextLineOf(text.edit),
    undo: (editor: Editor) => takeOutNewText(editor, text.newTextId),
  }));
  return [...spans, ...added];
}
