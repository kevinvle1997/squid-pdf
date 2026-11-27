// The page's lines: spans the server found on one baseline, a word space apart, whatever their style, edited as
// one field (`Document.lines`). A span on no line of more than one is a line of its own.
import type { Box, Document, FitRules, SpanInfo, Strategy } from "../api/types";
import { type LineScale, runsLaidOut, runWidthsOf, setAs } from "./fit";
import { type FontsByObject, fontOf } from "./spanFonts";

/** Spans one field edits, left to right, the box they make together, and how far its end can run. */
export interface Line {
  readonly id: string; // its first span's: its mark and its field stand there
  readonly spans: readonly SpanInfo[];
  readonly bbox: Box;
  readonly roomPt: number; // the least room any of its spans has, the line moving as one
  readonly atSplit?: boolean; // it ends at a split, so its room is the gap to the text there, with no tolerance
}

/** The fit rules `line` goes by: before the text at a split, with no tolerance, as `runs_past` in editing/fit.py. */
export function rulesFor(line: Pick<Line, "atSplit"> | undefined, rules: FitRules): FitRules {
  return line?.atSplit === true ? { ...rules, tolerance_pt: 0 } : rules;
}

/** Each line of more than one span the document lists, by each of its spans' ids: one object per line. */
export function linesBySpan(doc: Pick<Document, "spans" | "lines">): ReadonlyMap<string, Line> {
  const spans = new Map(doc.spans.map((span) => [span.id, span]));
  const lines = new Map<string, Line>();
  for (const listed of doc.lines) {
    const onLine = listed.span_ids.flatMap((id) => spans.get(id) ?? []);
    const [first] = onLine;
    // A line naming a span the document lacks is no line the server would take an edit of.
    if (first === undefined || onLine.length < 2 || onLine.length !== listed.span_ids.length) continue;
    const line: Line = { id: first.id, spans: onLine, bbox: unionOf(onLine), roomPt: listed.room_pt };
    for (const span of onLine) lines.set(span.id, line);
  }
  return lines;
}

// A run drawn further than this from where the page has it has moved: `MOVED_PT` in core/constants.py.
const MOVED_PT = 0.05;

/** The box `spans` cover together. */
export function unionOf(spans: readonly SpanInfo[]): Box {
  return {
    x0: Math.min(...spans.map(({ bbox }) => bbox.x0)),
    y0: Math.min(...spans.map(({ bbox }) => bbox.y0)),
    x1: Math.max(...spans.map(({ bbox }) => bbox.x1)),
    y1: Math.max(...spans.map(({ bbox }) => bbox.y1)),
  };
}

/** Whether `span` is the first of a line of more than one in `lines`, or on none: where a mark or field stands. */
export function startsALine(lines: ReadonlyMap<string, Line>, span: SpanInfo): boolean {
  return (lines.get(span.id)?.id ?? span.id) === span.id;
}

/**
 * `runs`' words with `start` to `end` of them all, read as one text, replaced by `typed`: the words typed go into the
 * run `runTypedInto` says, and whatever the range covers comes out of each run it reaches.
 */
export function runsEdited(
  runs: readonly string[],
  { start, end, typed }: { start: number; end: number; typed: string },
): string[] {
  const into = runTypedInto(runs, start);
  let runStart = 0;
  return runs.map((run, index) => {
    const from = Math.min(Math.max(start - runStart, 0), run.length);
    const to = Math.min(Math.max(end - runStart, 0), run.length);
    runStart += run.length;
    return run.slice(0, from) + (index === into ? typed : "") + run.slice(to);
  });
}

/**
 * The run words typed at `offset` of `runs`, read as one text, go into: the one it falls in; at the boundary between
 * two, the one before, as every editor does; at the line's start, the first. An emptied run is gone, and takes none.
 */
export function runTypedInto(runs: readonly string[], offset: number): number {
  let runEnd = 0;
  for (const [index, run] of runs.entries()) {
    runEnd += run.length;
    const fallsIn = offset > runEnd - run.length || offset === 0;
    if (run.length > 0 && offset <= runEnd && fallsIn) return index;
  }
  // Every run emptied: the line's first takes what's typed.
  return 0;
}

/**
 * Which of a line's runs an edit redraws, by whether each is `changed`: those from its first change to its last, and on
 * to the line's end where the change `movesTheRest`. Before any change, every one: any may be, and each says so first.
 */
export function runsRedrawn(changed: readonly boolean[], { movesTheRest }: { movesTheRest: boolean }): boolean[] {
  const first = changed.indexOf(true);
  if (first < 0) return changed.map(() => true);
  const last = movesTheRest ? changed.length - 1 : changed.lastIndexOf(true);
  return changed.map((_, index) => first <= index && index <= last);
}

/**
 * What a deletion of `kind` (an input event's type) takes of `text` with `start` to `end` selected: the selection;
 * with only a caret, the letter, the word or the line before it or after it, as a field's own keys take them.
 */
export function deletedBy(kind: string, text: string, [start, end]: readonly [number, number]): [number, number] {
  if (start !== end) return [start, end];
  if (kind === "deleteContentBackward") return [start - ([...text.slice(0, start)].at(-1)?.length ?? 0), start];
  if (kind === "deleteContentForward") return [start, start + ([...text.slice(start)][0]?.length ?? 0)];
  if (kind === "deleteWordBackward") return [wordStartBefore(text, start), start];
  if (kind === "deleteWordForward") return [start, wordEndAfter(text, start)];
  if (kind.endsWith("LineBackward")) return [0, start];
  if (kind.endsWith("LineForward")) return [start, text.length];
  return [start, end];
}

/** Where the word before `at` in `text` starts, past any spaces between: what a word's delete back takes to. */
function wordStartBefore(text: string, at: number): number {
  let from = at;
  while (from > 0 && /\s/.test(text[from - 1] ?? "")) from--;
  while (from > 0 && /\S/.test(text[from - 1] ?? "")) from--;
  return from;
}

/** Where the word after `at` in `text` ends, past any spaces between: what a word's delete forward takes to. */
function wordEndAfter(text: string, at: number): number {
  let to = at;
  while (to < text.length && /\s/.test(text[to] ?? "")) to++;
  while (to < text.length && /\S/.test(text[to] ?? "")) to++;
  return to;
}

/** A run of a line's edit as it's drawn: its span moved to where it starts now, its words, and how they're set. */
export interface DrawnRun {
  readonly span: SpanInfo; // as the document has it
  readonly moved: SpanInfo; // the same, its box where the run is drawn
  readonly text: string;
  readonly scale: LineScale;
  readonly redrawn: boolean; // changed, or moved by a change before it: the rest stays as the page has it
}

/**
 * `line`'s runs as an edit reading `texts` draws them, the way out `asked` while it's offered: where each starts, and
 * which the server redraws. None while one's font is unknown, which only the render can place.
 */
export function lineDrawnAs(
  line: Pick<Line, "spans" | "roomPt" | "atSplit">,
  {
    texts,
    fonts,
    asked,
    rules,
  }: { texts: readonly string[]; fonts: FontsByObject; asked: Strategy | undefined; rules: FitRules },
): DrawnRun[] | null {
  const typed = line.spans.flatMap((span, index) => {
    const font = fontOf(fonts, span);
    return font === undefined ? [] : [{ span, font, text: texts[index] ?? span.text }];
  });
  if (typed.length !== line.spans.length) return null;
  const widths = runWidthsOf(typed);
  const { way, fit, starts } = runsLaidOut(widths, { roomPt: line.roomPt, asked, rules: rulesFor(line, rules) });
  const set = setAs(way, fit);
  return typed.map(({ span, text }, index) => {
    const run = widths[index];
    const dx = (starts[index] ?? 0) - (run?.x ?? 0);
    const changed = run?.changed ?? false;
    const moved = { ...span, bbox: { ...span.bbox, x0: span.bbox.x0 + dx, x1: span.bbox.x1 + dx } };
    const redrawn = changed || Math.abs(dx) > MOVED_PT;
    return { span, moved, text, scale: changed ? set : { size: 1, scaleX: 1 }, redrawn };
  });
}
