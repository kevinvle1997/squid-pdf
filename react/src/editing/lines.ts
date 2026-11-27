// The page's lines: spans the server found on one baseline, a word space apart, whatever their style, edited as
// one field (`Document.lines`). A span on no line of more than one is a line of its own.
import type { Box, Document, SpanInfo } from "../api/types";

/** Spans one field edits, left to right, the box they make together, and how far its end can run. */
export interface Line {
  readonly id: string; // its first span's: its mark and its field stand there
  readonly spans: readonly SpanInfo[];
  readonly bbox: Box;
  readonly roomPt: number; // the least room any of its spans has, the line moving as one
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

/** The box `spans` cover together. */
function unionOf(spans: readonly SpanInfo[]): Box {
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
