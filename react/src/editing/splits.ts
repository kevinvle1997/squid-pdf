// A split ends a field at the caret, the text past it staying put: each side of a live edit's or the open field's
// edge is a line of its own. A cut span's parts go by the server's keys; the history keeps the span, `start`, `end`.
import type { Edit, Redact, Replace, ReplaceRuns, SpanInfo } from "../api/types";
import { lineWidthPt } from "./fit";
import type { Entry } from "./history";
import { type Line, unionOf } from "./lines";
import { type FontsByObject, fontOf } from "./spanFonts";

/** A stretch of a line's text, in code points of its spans' words read as one, from `from` up to `to`. */
export interface Region {
  readonly lineId: string; // the line's first span's id, or a span on no line of more than one
  readonly from: number;
  readonly to: number;
}

/** Which code points of which span a part is. */
export interface Part {
  readonly spanId: string;
  readonly start: number;
  readonly end: number;
}

/** The unsplit layout's pieces a split is worked out from. */
export interface Unsplit {
  readonly spans: ReadonlyMap<string, SpanInfo>;
  readonly lines: ReadonlyMap<string, Line>;
  readonly fonts: FontsByObject;
}

/** A line as the document has it: one of its lines of more than one span, or a span alone. */
interface RealLine {
  readonly id: string;
  readonly spans: readonly SpanInfo[];
  readonly roomPt: number;
}

/** The id a part is known by: its span's, and where it starts after a split. */
export function partKey(spanId: string, start: number): string {
  return start > 0 ? `${spanId}@${start}` : spanId;
}

/** The span a part's id names. */
export function spanIdOf(key: string): string {
  const at = key.indexOf("@");
  return at < 0 ? key : key.slice(0, at);
}

/** How many code points `text` has, as the server counts a span's. */
export function codePointsIn(text: string): number {
  return [...text].length;
}

/** `span`'s line as the document has it; none for a span it lacks. */
function realLineOf(unsplit: Unsplit, spanId: string): RealLine | undefined {
  const line = unsplit.lines.get(spanId);
  if (line !== undefined) return line;
  const span = unsplit.spans.get(spanId);
  return span === undefined ? undefined : { id: span.id, spans: [span], roomPt: span.room_pt };
}

/** How many code points of its line come before `spanId`. */
function offsetOf(line: RealLine, spanId: string): number {
  let offset = 0;
  for (const span of line.spans) {
    if (span.id === spanId) return offset;
    offset += codePointsIn(span.text);
  }
  return offset;
}

/** How many code points `line`'s text has. */
function lengthOf(line: RealLine): number {
  return line.spans.reduce((total, span) => total + codePointsIn(span.text), 0);
}

/** The text an edit of text takes over, as a region of its line; none for another kind, or a span unknown. */
export function regionOf(unsplit: Unsplit, edit: Edit): Region | null {
  if (edit.kind === "replace" || edit.kind === "redact") {
    const line = realLineOf(unsplit, edit.span_id);
    const span = unsplit.spans.get(edit.span_id);
    if (line === undefined || span === undefined) return null;
    const from = offsetOf(line, span.id);
    return { lineId: line.id, from, to: from + codePointsIn(span.text) };
  }
  if (edit.kind !== "replace_runs") return null;
  const first = edit.runs[0];
  const last = edit.runs.at(-1);
  const line = first === undefined ? undefined : realLineOf(unsplit, first.span_id);
  const lastSpan = last === undefined ? undefined : unsplit.spans.get(last.span_id);
  if (first === undefined || last === undefined || line === undefined || lastSpan === undefined) return null;
  const from = offsetOf(line, first.span_id) + (first.start ?? 0);
  const to = offsetOf(line, lastSpan.id) + (last.end ?? codePointsIn(lastSpan.text));
  return { lineId: line.id, from, to };
}

/** Whether two regions share some text. */
function overlaps(a: Region, b: Region): boolean {
  return a.lineId === b.lineId && a.from < b.to && b.from < a.to;
}

/**
 * `entries` less each replace a later edit overlaps, which is the last word on that text, or one the open field's
 * split `taken` overlaps: the field's words stand for it while it's open.
 */
export function liveEntries(unsplit: Unsplit, entries: readonly Entry[], taken: Region | null): readonly Entry[] {
  const regions = entries.map(({ edit }) => regionOf(unsplit, edit));
  const overridden = (index: number) => {
    const region = regions[index];
    const edit = entries[index]?.edit;
    if (region === null || region === undefined || edit?.kind === "redact") return false;
    if (taken !== null && overlaps(region, taken)) return true;
    return regions.slice(index + 1).some((later) => later !== null && overlaps(region, later));
  };
  const live = entries.filter((_, index) => !overridden(index));
  return live.length === entries.length ? entries : live;
}

/** Where each line is split, by its id, in order: each live edit's bounds inside its line, and the open field's. */
export function cutsOf(unsplit: Unsplit, live: readonly Entry[], taken: Region | null): Map<string, number[]> {
  const regions = live.flatMap(({ edit }) => (edit.kind === "redact" ? [] : (regionOf(unsplit, edit) ?? [])));
  const cuts = new Map<string, Set<number>>();
  for (const region of [...regions, ...(taken === null ? [] : [taken])]) {
    const line = realLineOf(unsplit, region.lineId);
    if (line === undefined) continue;
    const inside = [region.from, region.to].filter((at) => at > 0 && at < lengthOf(line));
    if (inside.length === 0) continue;
    const known = cuts.get(line.id) ?? new Set();
    for (const at of inside) known.add(at);
    cuts.set(line.id, known);
  }
  return new Map([...cuts].map(([id, at]) => [id, [...at].sort((a, b) => a - b)]));
}

/** What a split layout makes of an unsplit one: each cut span's parts, and each side of a split as a line. */
export interface SplitSpans {
  readonly spans: ReadonlyMap<string, SpanInfo>; // by id, a cut span's parts by theirs
  readonly ordered: readonly SpanInfo[]; // in the document's order, parts in their span's place
  readonly lines: ReadonlyMap<string, Line>; // a side of a split, however few its spans, is a line
  readonly parts: ReadonlyMap<string, Part>;
}

/** The unsplit pieces split at `cuts`; the unsplit ones where a font that places a part is unknown. */
export function splitSpans(
  unsplit: Unsplit & { ordered: readonly SpanInfo[] },
  cuts: ReadonlyMap<string, readonly number[]>,
): SplitSpans {
  const spans = new Map(unsplit.spans);
  const lines = new Map(unsplit.lines);
  const parts = new Map<string, Part>();
  const partsOf = new Map<string, SpanInfo[]>(); // each cut span's, in order
  for (const [lineId, at] of cuts) {
    const line = realLineOf(unsplit, lineId);
    if (line === undefined || line.spans.some((span) => fontOf(unsplit.fonts, span) === undefined)) continue;
    const edges = [0, ...at, lengthOf(line)];
    for (const span of line.spans) {
      lines.delete(span.id);
      spans.delete(span.id);
    }
    for (const [index, from] of edges.slice(0, -1).entries()) {
      const to = edges[index + 1] ?? from;
      const cut = partsIn(unsplit, line, { from, to });
      const room = roomOf(unsplit, line, { to, last: cut.at(-1) });
      const first = cut[0];
      if (first === undefined) continue;
      const onSide = cut.map(({ part, key }) => {
        // A part short of its span at either end goes to the server by where it's cut.
        const whole = part.start === 0 && part.end === codePointsIn(unsplit.spans.get(part.spanId)?.text ?? "");
        if (!whole) parts.set(key, part);
        return partSpanOf(unsplit, { part, key, roomPt: room.roomPt });
      });
      const side: Line = {
        id: first.key,
        spans: onSide,
        bbox: unionOf(onSide),
        roomPt: room.roomPt,
        atSplit: room.atSplit,
      };
      for (const each of onSide) {
        spans.set(each.id, each);
        lines.set(each.id, side);
        const realId = spanIdOf(each.id);
        partsOf.set(realId, [...(partsOf.get(realId) ?? []), each]);
      }
    }
  }
  const ordered = unsplit.ordered.flatMap((span) => partsOf.get(span.id) ?? [span]);
  return { spans, ordered, lines, parts };
}

/** Each span `line` has some of from `from` to `to`, as that part of it. */
function partsIn(
  unsplit: Unsplit,
  line: RealLine,
  { from, to }: { from: number; to: number },
): { part: Part; key: string }[] {
  return line.spans.flatMap((span) => {
    const offset = offsetOf(line, span.id);
    const length = codePointsIn(unsplit.spans.get(span.id)?.text ?? span.text);
    const start = Math.max(from - offset, 0);
    const end = Math.min(to - offset, length);
    return start < end ? [{ part: { spanId: span.id, start, end }, key: partKey(span.id, start) }] : [];
  });
}

/** Where code point `at` of `span`'s text starts, by its font's widths, as the preview places it. */
function xAt(unsplit: Unsplit, span: SpanInfo, at: number): number {
  const length = codePointsIn(span.text);
  if (at <= 0) return span.origin[0] ?? span.bbox.x0;
  if (at >= length) return span.bbox.x1;
  const glyphs = fontOf(unsplit.fonts, span)?.glyphs ?? {};
  const before = [...span.text].slice(0, at).join("");
  return (span.origin[0] ?? span.bbox.x0) + lineWidthPt(span, before, glyphs);
}

/** One part as a span of its own: its words, from where they start to where they end. */
function partSpanOf(unsplit: Unsplit, { part, key, roomPt }: { part: Part; key: string; roomPt: number }): SpanInfo {
  const span = unsplit.spans.get(part.spanId) as SpanInfo;
  const whole = part.start === 0 && part.end === codePointsIn(span.text);
  const x0 = xAt(unsplit, span, part.start);
  const x1 = xAt(unsplit, span, part.end);
  const text = [...span.text].slice(part.start, part.end).join("");
  const baseline = span.origin[1] ?? span.bbox.y1;
  const placed = whole
    ? {}
    : { text, bbox: { ...span.bbox, x0: part.start === 0 ? span.bbox.x0 : x0, x1 }, origin: [x0, baseline] };
  return { ...span, ...placed, id: key, room_pt: roomPt };
}

/**
 * The room a side of a split ending at `to` has: at the line's end, the line's own; before a split, the gap to the
 * text after it, with no tolerance, since it's right there. The server reads that from the page's letters.
 */
function roomOf(
  unsplit: Unsplit,
  line: RealLine,
  { to, last }: { to: number; last: { part: Part } | undefined },
): { roomPt: number; atSplit: boolean } {
  if (to >= lengthOf(line) || last === undefined) return { roomPt: line.roomPt, atSplit: false };
  const lastSpan = unsplit.spans.get(last.part.spanId) as SpanInfo;
  const endX = xAt(unsplit, lastSpan, last.part.end);
  const rest = [...lastSpan.text].slice(last.part.end);
  const spaces = rest.findIndex((letter) => letter.trim() !== "");
  const next = line.spans[line.spans.indexOf(lastSpan) + 1];
  const nextX = spaces >= 0 ? xAt(unsplit, lastSpan, last.part.end + spaces) : next?.origin[0];
  if (nextX === undefined) return { roomPt: line.roomPt, atSplit: false };
  return { roomPt: Math.max(nextX - endX, 0), atSplit: true };
}

/** `edit` as the wire says it: a part's edit becomes one of its span, from where the part starts to where it ends. */
export function wireEdit(unsplit: Unsplit, parts: ReadonlyMap<string, Part>, edit: Edit): Edit {
  if (edit.kind === "replace") return wireReplace(parts, edit);
  if (edit.kind === "replace_runs") return wireRuns(unsplit, parts, edit);
  if (edit.kind === "redact") return wireRedact(parts, edit);
  return edit;
}

/** A part's replace, as a line's edit of that part alone. */
function wireReplace(parts: ReadonlyMap<string, Part>, edit: Replace): Edit {
  const part = parts.get(edit.span_id);
  if (part === undefined) return edit;
  const { kind: _kind, span_id: _span, text, ...rest } = edit;
  const run = { span_id: part.spanId, text, start: part.start, end: part.end };
  return { ...rest, kind: "replace_runs", runs: [run] };
}

/** A side's edit, each part named by its span, the first's start and the last's end where they cut one. */
function wireRuns(unsplit: Unsplit, parts: ReadonlyMap<string, Part>, edit: ReplaceRuns): ReplaceRuns {
  if (!edit.runs.some((run) => parts.has(run.span_id))) return edit;
  const runs = edit.runs.map((run, index) => {
    const part = parts.get(run.span_id);
    if (part === undefined) return run;
    const span = unsplit.spans.get(part.spanId);
    const start = index === 0 && part.start > 0 ? part.start : undefined;
    const endsInside = span !== undefined && part.end < codePointsIn(span.text);
    const end = index === edit.runs.length - 1 && endsInside ? part.end : undefined;
    return {
      span_id: part.spanId,
      text: run.text,
      ...(start !== undefined && { start }),
      ...(end !== undefined && { end }),
    };
  });
  return { ...edit, runs };
}

/** A part's redaction, as one of its span's words: all of the part where it names no range. */
function wireRedact(parts: ReadonlyMap<string, Part>, edit: Redact): Redact {
  const part = parts.get(edit.span_id);
  if (part === undefined) return edit;
  const [from, to] = edit.range ?? [0, part.end - part.start];
  return { ...edit, span_id: part.spanId, range: [part.start + from, part.start + to] };
}
