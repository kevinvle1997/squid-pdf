import type { Document, Edit, Insert, PageInfo, Redact, Replace, ReplaceRuns, SpanInfo, Strategy } from "../api/types";
import { type Entry, newTextIdsOf, spansOf } from "./history";
import { partKey } from "./splits";

/** By code point, as the server counts. */
export type CodePoints = NonNullable<Redact["range"]>;

/** In the field's units: UTF-16, as JavaScript counts. */
export interface RedactedRange {
  readonly start: number;
  readonly end: number;
}

export type Redacted = "whole" | readonly RedactedRange[];

export interface SpanReading {
  readonly span: SpanInfo;
  readonly text: string;
  readonly replaced: boolean;
  readonly strategy: Strategy | undefined;
  readonly redacted: Redacted | null;
  readonly lineEdit?: ReplaceRuns;
}

export interface NewTextReading {
  readonly id: number;
  readonly newTextId: number;
  readonly edit: Insert;
}

export interface PageEdits {
  readonly spans: readonly SpanReading[];
  readonly newTexts: readonly NewTextReading[];
}

export interface Reading {
  readonly spans: ReadonlyMap<string, SpanReading>;
  readonly pages: ReadonlyMap<number, PageEdits>;
  readonly edits: readonly Edit[];
  readonly order: readonly number[];
  readonly deleted: ReadonlySet<number>;
}

const NONE_DELETED: ReadonlySet<number> = new Set();

export const UNEDITED: Reading = { spans: new Map(), pages: new Map(), edits: [], order: [], deleted: NONE_DELETED };

/** As the server folds them: a page it lacks, one deleted already, and the last page left are skipped. */
export function pageOrder(pageCount: number, edits: readonly Edit[]): number[] {
  const order = Array.from({ length: pageCount }, (_, page) => page);
  for (const edit of edits) {
    if (edit.kind !== "delete_page" || order.length === 1 || !order.includes(edit.page)) continue;
    order.splice(order.indexOf(edit.page), 1);
  }
  return order;
}

export function shownNumberOf(order: readonly number[], page: number): number {
  return Math.min(order.filter((kept) => kept < page).length + 1, Math.max(order.length, 1));
}

export interface ShownPage {
  readonly page: number;
  readonly info: PageInfo;
  readonly shownNumber: number | null;
  readonly splitAfter: boolean;
}

export function pagesShown(order: readonly number[], pages: readonly PageInfo[]): ShownPage[] {
  return pages.map((info, page) => {
    const index = order.indexOf(page);
    if (index < 0) return { page, info, shownNumber: null, splitAfter: false };
    return { page, info, shownNumber: index + 1, splitAfter: index < order.length - 1 };
  });
}

export function textIn(reading: Reading, span: SpanInfo): string {
  return reading.spans.get(span.id)?.text ?? span.text;
}

export function sameSpan(a: SpanReading | undefined, b: SpanReading | undefined): boolean {
  if (a === b) return true;
  if (a === undefined || b === undefined) return false;
  // A part a split moved is another object, drawn elsewhere.
  const same =
    a.span === b.span && a.text === b.text && sameRedacted(a.redacted, b.redacted) && a.strategy === b.strategy;
  // A line's run moves as the others are typed, so it draws the same only while every run of its line does.
  return same && sameRuns(a.lineEdit, b.lineEdit);
}

function sameRuns(a: ReplaceRuns | undefined, b: ReplaceRuns | undefined): boolean {
  if (a === b) return true;
  return (
    a !== undefined &&
    b !== undefined &&
    a.runs.length === b.runs.length &&
    a.runs.every((run, index) => run.text === b.runs[index]?.text)
  );
}

function sameRedacted(a: Redacted | null, b: Redacted | null): boolean {
  if (typeof a !== "object" || typeof b !== "object" || a === null || b === null) return a === b;
  return a.length === b.length && a.every((range, index) => sameRange(range, b[index]));
}

export function sameRange(a: RedactedRange, b: RedactedRange | undefined): boolean {
  return a.start === b?.start && a.end === b?.end;
}

export function unitsOf(text: string, [start, end]: CodePoints): RedactedRange {
  const letters = [...text];
  return { start: letters.slice(0, start).join("").length, end: letters.slice(0, end).join("").length };
}

export function redactedRangesOf(reading: SpanReading | undefined): readonly RedactedRange[] {
  const redacted = reading?.redacted ?? null;
  return redacted === null || redacted === "whole" ? [] : redacted;
}

export function redactedBetween(text: string, { from, to }: { from: Redacted | null; to: Redacted | null }): string {
  const taken = (redacted: Redacted | null, at: number) =>
    redacted === "whole" || (redacted ?? []).some(({ start, end }) => start <= at && at < end);
  const runs: string[] = [];
  let run = "";
  for (let at = 0; at < text.length; at++) {
    if (taken(from, at) && !taken(to, at)) {
      run += text[at];
      continue;
    }
    if (run.trim() !== "") runs.push(run.trim());
    run = "";
  }
  if (run.trim() !== "") runs.push(run.trim());
  return runs.join(" … ");
}

export function wordsAt(text: string, [start, end]: CodePoints): string {
  return [...text].slice(start, end).join("");
}

function redactedBy(span: SpanInfo, redactions: readonly Redact[] | undefined): Redacted | null {
  if (redactions === undefined) return null;
  const ranges: RedactedRange[] = [];
  for (const { range } of redactions) {
    if (range == null) return "whole";
    ranges.push(unitsOf(span.text, range));
  }
  const merged = mergedRanges(ranges);
  const all = merged.length === 1 && merged[0]?.start === 0 && merged[0]?.end === span.text.length;
  return all ? "whole" : merged;
}

/** As the server merges a span's ranges: `tests/editing/redacted_words_cases.json` holds both to it. */
function mergedRanges(ranges: readonly RedactedRange[]): RedactedRange[] {
  const merged: RedactedRange[] = [];
  for (const range of [...ranges].sort((a, b) => a.start - b.start)) {
    const last = merged.at(-1);
    const meetsLast = last !== undefined && range.start <= last.end;
    if (meetsLast) {
      merged[merged.length - 1] = { ...last, end: Math.max(last.end, range.end) };
      continue;
    }
    merged.push(range);
  }
  return merged;
}

export function samePage(a: PageEdits | undefined, b: PageEdits | undefined): boolean {
  if (a === b) return true;
  if (a === undefined || b === undefined) return false;
  return (
    a.spans.length === b.spans.length &&
    a.spans.every((span, index) => sameSpan(span, b.spans[index])) &&
    a.newTexts.length === b.newTexts.length &&
    a.newTexts.every((insert, index) => insert.id === b.newTexts[index]?.id)
  );
}

export type Differing = Pick<SpanReading, "span" | "text" | "redacted" | "strategy" | "lineEdit">;

export function differing(now: PageEdits | undefined, then: PageEdits | undefined): Differing[] {
  const was = new Map(then?.spans.map((reading) => [reading.span.id, reading]));
  const out: Differing[] = [];
  for (const reading of now?.spans ?? []) {
    const { span, text, redacted, strategy, lineEdit } = reading;
    if (!sameSpan(reading, was.get(span.id))) out.push({ span, text, redacted, strategy, lineEdit });
    was.delete(span.id);
  }
  for (const reading of was.values()) {
    out.push({ span: reading.span, text: reading.span.text, redacted: null, strategy: undefined });
  }
  return out;
}

export function project(
  doc: Pick<Document, "spans" | "pages">,
  entries: readonly Entry[],
  previous = UNEDITED,
): Reading {
  const { spans } = doc;
  const replaces = new Map<string, Replace | ReplaceRuns>();
  const redactions = new Map<string, Redact[]>();
  const latest = new Map<number, NewTextReading>();
  const newTextIds = newTextIdsOf(entries);
  for (const { id, edit } of entries) {
    switch (edit.kind) {
      case "replace":
      case "replace_runs":
        keepLast(replaces, edit);
        break;
      case "redact":
        if (replaces.get(edit.span_id)?.kind === "replace_runs") forget(replaces, edit.span_id);
        redactions.set(edit.span_id, [...(redactions.get(edit.span_id) ?? []), edit]);
        break;
      case "insert": {
        const newTextId = newTextIds.get(id) ?? id;
        latest.delete(newTextId);
        latest.set(newTextId, { id, newTextId, edit });
        break;
      }
      case "delete_page":
        break;
      default:
        edit satisfies never;
    }
  }

  const readings = new Map<string, SpanReading>();
  const byPage = new Map<number, { spans: SpanReading[]; newTexts: NewTextReading[] }>();
  const onPage = (page: number) => {
    const edits = byPage.get(page) ?? { spans: [], newTexts: [] };
    byPage.set(page, edits);
    return edits;
  };
  const live = new Set<Edit>();
  const changed = changedLines(spans, replaces);
  for (const span of spans) {
    const replace = replaces.get(span.id);
    const text = textOf(replace, span);
    const replaced = text !== span.text;
    const lineEdit = replace?.kind === "replace_runs" && changed.has(replace) ? replace : undefined;
    if (replace !== undefined && (replaced || lineEdit !== undefined)) live.add(replace);
    const redacted = redactedBy(span, redactions.get(span.id));
    if (!replaced && redacted === null && lineEdit === undefined) continue;
    const strategy = replaced || lineEdit !== undefined ? replace?.strategy : undefined;
    const reading: SpanReading = { span, text, replaced, strategy, redacted, ...(lineEdit && { lineEdit }) };
    const was = previous.spans.get(span.id);
    const kept = was !== undefined && sameSpan(was, reading) ? was : reading;
    readings.set(span.id, kept);
    onPage(span.page).spans.push(kept);
  }
  const newTexts = [...latest.values()].filter(({ edit }) => edit.text.trim() !== "");
  for (const insert of newTexts) onPage(insert.edit.page).newTexts.push(insert);
  for (const insert of newTexts) live.add(insert.edit);

  const pages = new Map<number, PageEdits>();
  for (const [page, edits] of [...byPage].sort(([a], [b]) => a - b)) {
    const was = previous.pages.get(page);
    pages.set(page, was !== undefined && samePage(was, edits) ? was : edits);
  }

  const overridable = (edit: Edit) => edit.kind === "replace" || edit.kind === "replace_runs" || edit.kind === "insert";
  const sent = entries.filter(({ edit }) => !overridable(edit) || live.has(edit));
  const edits = sent.map((entry) => entry.edit);
  return { spans: readings, pages, edits, ...pagesKept(doc.pages.length, { edits, previous }) };
}

function keepLast(replaces: Map<string, Replace | ReplaceRuns>, edit: Replace | ReplaceRuns): void {
  const spanIds = spansOf(edit);
  for (const spanId of spanIds) forget(replaces, spanId);
  for (const spanId of spanIds) replaces.set(spanId, edit);
}

function forget(replaces: Map<string, Replace | ReplaceRuns>, spanId: string): void {
  const last = replaces.get(spanId);
  if (last === undefined) return;
  for (const each of spansOf(last)) replaces.delete(each);
}

function textOf(replace: Replace | ReplaceRuns | undefined, span: SpanInfo): string {
  if (replace === undefined) return span.text;
  if (replace.kind === "replace") return replace.text;
  return replace.runs.find((run) => partKey(run.span_id, run.start ?? 0) === span.id)?.text ?? span.text;
}

function changedLines(spans: readonly SpanInfo[], replaces: ReadonlyMap<string, Replace | ReplaceRuns>) {
  const changed = new Set<ReplaceRuns>();
  for (const span of spans) {
    const replace = replaces.get(span.id);
    if (replace?.kind === "replace_runs" && textOf(replace, span) !== span.text) changed.add(replace);
  }
  return changed;
}

function pagesKept(
  pageCount: number,
  { edits, previous }: { edits: readonly Edit[]; previous: Reading },
): Pick<Reading, "order" | "deleted"> {
  const order = pageOrder(pageCount, edits);
  const same = order.length === previous.order.length && order.every((page, index) => page === previous.order[index]);
  if (same) return { order: previous.order, deleted: previous.deleted };
  const deleted = Array.from({ length: pageCount }, (_, page) => page).filter((page) => !order.includes(page));
  return { order, deleted: deleted.length === 0 ? NONE_DELETED : new Set(deleted) };
}
