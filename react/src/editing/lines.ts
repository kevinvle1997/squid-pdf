import type { Box, Document, FitRules, SpanInfo, Strategy } from "../api/types";
import { type LineScale, runsLaidOut, runWidthsOf, setAs } from "./fit";
import { type FontsByObject, fontOf } from "./spanFonts";

export interface Line {
  readonly id: string;
  readonly spans: readonly SpanInfo[];
  readonly bbox: Box;
  readonly roomPt: number; // the least room any of its spans has, the line moving as one
  readonly atSplit?: boolean; // it ends at a split, so its room is the gap to the text there, with no tolerance
}

/** The fit rules `line` goes by: before the text at a split, with no tolerance, as `runs_past` in editing/fit.py. */
export function rulesFor(line: Pick<Line, "atSplit"> | undefined, rules: FitRules): FitRules {
  return line?.atSplit === true ? { ...rules, tolerance_pt: 0 } : rules;
}

export function linesBySpan(doc: Pick<Document, "spans" | "lines">): ReadonlyMap<string, Line> {
  const spans = new Map(doc.spans.map((span) => [span.id, span]));
  const lines = new Map<string, Line>();
  for (const listed of doc.lines) {
    const onLine = listed.span_ids.flatMap((id) => spans.get(id) ?? []);
    const [first] = onLine;
    if (first === undefined || onLine.length < 2 || onLine.length !== listed.span_ids.length) continue;
    const line: Line = { id: first.id, spans: onLine, bbox: unionOf(onLine), roomPt: listed.room_pt };
    for (const span of onLine) lines.set(span.id, line);
  }
  return lines;
}

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

export function startsALine(lines: ReadonlyMap<string, Line>, span: SpanInfo): boolean {
  return (lines.get(span.id)?.id ?? span.id) === span.id;
}

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

export function runTypedInto(runs: readonly string[], offset: number): number {
  let runEnd = 0;
  for (const [index, run] of runs.entries()) {
    runEnd += run.length;
    const fallsIn = offset > runEnd - run.length || offset === 0;
    if (run.length > 0 && offset <= runEnd && fallsIn) return index;
  }
  return 0;
}

export function runsRedrawn(changed: readonly boolean[], { movesTheRest }: { movesTheRest: boolean }): boolean[] {
  const first = changed.indexOf(true);
  if (first < 0) return changed.map(() => true);
  const last = movesTheRest ? changed.length - 1 : changed.lastIndexOf(true);
  return changed.map((_, index) => first <= index && index <= last);
}

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

function wordStartBefore(text: string, at: number): number {
  let from = at;
  while (from > 0 && /\s/.test(text[from - 1] ?? "")) from--;
  while (from > 0 && /\S/.test(text[from - 1] ?? "")) from--;
  return from;
}

function wordEndAfter(text: string, at: number): number {
  let to = at;
  while (to < text.length && /\s/.test(text[to] ?? "")) to++;
  while (to < text.length && /\S/.test(text[to] ?? "")) to++;
  return to;
}

export interface DrawnRun {
  readonly span: SpanInfo;
  readonly moved: SpanInfo;
  readonly text: string;
  readonly scale: LineScale;
  readonly redrawn: boolean;
}

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
