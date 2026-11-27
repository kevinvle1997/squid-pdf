// The one reading of the history that what's drawn, rendered and exported agree on. An object nothing
// changed stays the same object from one reading to the next, so whatever draws it can skip it.
import type { Document, Edit, Insert, PageInfo, Redact, Replace, ReplaceRuns, SpanInfo, Strategy } from "../api/types";
import { type Entry, newTextIdsOf, spansOf } from "./history";
import { partKey } from "./splits";

/** A range of a span's text by code point, as the server counts it: what a redaction names. */
export type CodePoints = NonNullable<Redact["range"]>;

/** The range of a run of a span's redacted words, `start` to `end` in the field's units: UTF-16, as JavaScript counts. */
export interface RedactedRange {
  readonly start: number;
  readonly end: number;
}

/** What redactions take of a span: all of it, or the ranges of runs of its words, in order, none meeting another. */
export type Redacted = "whole" | readonly RedactedRange[];

export interface SpanReading {
  readonly span: SpanInfo;
  readonly text: string; // what it reads now
  readonly replaced: boolean; // it reads other than the original
  readonly strategy: Strategy | undefined; // how a replacement too long is to fit, when one is asked for
  readonly redacted: Redacted | null; // what's to be taken out of the file, if anything
  readonly lineEdit?: ReplaceRuns; // the line's edit it's a run of: any of its runs may be moved, so each is drawn from it
}

/** A piece of new text as its latest version has it. */
export interface NewTextReading {
  readonly id: number; // its latest version's edit's, in the history
  readonly newTextId: number; // its first version's: the same however it's moved or retyped
  readonly edit: Insert;
}

/** One page's edits. */
export interface PageEdits {
  readonly spans: readonly SpanReading[]; // in the document's order
  readonly newTexts: readonly NewTextReading[]; // in the order made
}

export interface Reading {
  readonly spans: ReadonlyMap<string, SpanReading>; // only the spans an edit changes, on every page, deleted or not
  readonly pages: ReadonlyMap<number, PageEdits>; // only the pages with an edit
  readonly edits: readonly Edit[]; // what the server is sent: only what changes the file
  readonly order: readonly number[]; // the pages the download has, in order, by their numbers in the original
  readonly deleted: ReadonlySet<number>; // the pages a delete leaves out of it
}

const NONE_DELETED: ReadonlySet<number> = new Set();

/** No reading yet: what the first is compared with. */
export const UNEDITED: Reading = { spans: new Map(), pages: new Map(), edits: [], order: [], deleted: NONE_DELETED };

/**
 * The pages a download of `pageCount` pages keeps after `edits`, in order, as the server folds them
 * (`_PageOps` in editing/apply.py): a page it doesn't have, one deleted already, and the last page left are skipped.
 */
export function pageOrder(pageCount: number, edits: readonly Edit[]): number[] {
  const order = Array.from({ length: pageCount }, (_, page) => page);
  for (const edit of edits) {
    // A download needs a page, and a page gone can't go again.
    if (edit.kind !== "delete_page" || order.length === 1 || !order.includes(edit.page)) continue;
    order.splice(order.indexOf(edit.page), 1);
  }
  return order;
}

/** Page `page`'s number among the pages shown, counting from 1: a deleted one's, as it would be put back. */
export function shownNumberOf(order: readonly number[], page: number): number {
  // Only a delete changes the order, so the pages kept stay in the original's.
  return Math.min(order.filter((kept) => kept < page).length + 1, Math.max(order.length, 1));
}

/** One of the document's pages as the pages are shown. */
export interface ShownPage {
  readonly page: number; // its number in the original, counting from 0
  readonly info: PageInfo;
  readonly shownNumber: number | null; // its number among the pages shown, from 1; null while it's deleted
  readonly splitAfter: boolean; // a page shown follows it, so Split here goes under it
}

/** Each of the document's `pages` as shown once `order` is kept: a deleted one has no number, and no Split here beside it. */
export function pagesShown(order: readonly number[], pages: readonly PageInfo[]): ShownPage[] {
  return pages.map((info, page) => {
    const index = order.indexOf(page);
    if (index < 0) return { page, info, shownNumber: null, splitAfter: false };
    return { page, info, shownNumber: index + 1, splitAfter: index < order.length - 1 };
  });
}

/** What `span` reads in `reading`: its edit's words, else the document's own. */
export function textIn(reading: Reading, span: SpanInfo): string {
  return reading.spans.get(span.id)?.text ?? span.text;
}

/** Whether two readings of a span draw the same. */
export function sameSpan(a: SpanReading | undefined, b: SpanReading | undefined): boolean {
  if (a === b) return true;
  if (a === undefined || b === undefined) return false;
  // A part a split moved is another object, drawn elsewhere.
  const same =
    a.span === b.span && a.text === b.text && sameRedacted(a.redacted, b.redacted) && a.strategy === b.strategy;
  // A line's run moves as the others are typed, so it draws the same only while every run of its line does.
  return same && sameRuns(a.lineEdit, b.lineEdit);
}

/** Whether two line edits read the same runs the same. */
function sameRuns(a: ReplaceRuns | undefined, b: ReplaceRuns | undefined): boolean {
  if (a === b) return true;
  return (
    a !== undefined &&
    b !== undefined &&
    a.runs.length === b.runs.length &&
    a.runs.every((run, index) => run.text === b.runs[index]?.text)
  );
}

/** Whether two redactions of a span take the same: all of it, or the same ranges of words. */
function sameRedacted(a: Redacted | null, b: Redacted | null): boolean {
  if (typeof a !== "object" || typeof b !== "object" || a === null || b === null) return a === b;
  return a.length === b.length && a.every((range, index) => sameRange(range, b[index]));
}

/** Whether two ranges of a span's words are the same. */
export function sameRange(a: RedactedRange, b: RedactedRange | undefined): boolean {
  return a.start === b?.start && a.end === b?.end;
}

/** The field's units of `text` that `range` takes: by code point, as the server counts, from `start` to `end`. */
export function unitsOf(text: string, [start, end]: CodePoints): RedactedRange {
  const letters = [...text];
  return { start: letters.slice(0, start).join("").length, end: letters.slice(0, end).join("").length };
}

/** The ranges of words redacted of a span as it reads: none when it's redacted whole, or not at all. */
export function redactedRangesOf(reading: SpanReading | undefined): readonly RedactedRange[] {
  const redacted = reading?.redacted ?? null;
  return redacted === null || redacted === "whole" ? [] : redacted;
}

/**
 * The words of `text` redacted in `from` and not in `to`, a run each, an ellipsis between: what an undo puts back, or
 * a redo takes out.
 */
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

/** The words of `text` from `start` to `end`, by code point, as a redaction's range names them. */
export function wordsAt(text: string, [start, end]: CodePoints): string {
  return [...text].slice(start, end).join("");
}

/** What `redactions` of `span` take, in the order its text reads: all of it, or its ranges merged; null with none. */
function redactedBy(span: SpanInfo, redactions: readonly Redact[] | undefined): Redacted | null {
  if (redactions === undefined) return null;
  const ranges: RedactedRange[] = [];
  for (const { range } of redactions) {
    // One redaction of all of it takes in the rest.
    if (range == null) return "whole";
    ranges.push(unitsOf(span.text, range));
  }
  const merged = mergedRanges(ranges);
  const all = merged.length === 1 && merged[0]?.start === 0 && merged[0]?.end === span.text.length;
  return all ? "whole" : merged;
}

/**
 * `ranges` in the text's order, those that overlap or meet as one, as the server merges a span's ranges:
 * `tests/editing/redacted_words_cases.json` holds both to it.
 */
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

/** Whether a page's edits draw the same in two readings. */
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

/** A span that reads differently than its page's strips show: what it reads now, or that it's redacted. */
export type Differing = Pick<SpanReading, "span" | "text" | "redacted" | "strategy" | "lineEdit">;

/**
 * The spans that read differently in `now` than in `then`, with what each reads now: the
 * original's words where `now` has put it back.
 */
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

/**
 * The document's spans with `entries` made, in the order made. `previous` is the last reading:
 * whatever reads the same is taken from it as it was.
 */
export function project(
  doc: Pick<Document, "spans" | "pages">,
  entries: readonly Entry[],
  previous = UNEDITED,
): Reading {
  const { spans } = doc;
  // The last replace to a span wins, as on the server; a redaction is for good, with any others of the span.
  const replaces = new Map<string, Replace | ReplaceRuns>();
  const redactions = new Map<string, Redact[]>();
  // New text's latest version wins, as a span's last replace does: by its first version's id, in the order last made.
  const latest = new Map<number, NewTextReading>();
  const newTextIds = newTextIdsOf(entries);
  for (const { id, edit } of entries) {
    switch (edit.kind) {
      case "replace":
      case "replace_runs":
        keepLast(replaces, edit);
        break;
      case "redact":
        // A line is drawn whole or not at all: one of its spans redacted, its edit is gone from every one.
        if (replaces.get(edit.span_id)?.kind === "replace_runs") forget(replaces, edit.span_id);
        redactions.set(edit.span_id, [...(redactions.get(edit.span_id) ?? []), edit]);
        break;
      case "insert": {
        const newTextId = newTextIds.get(id) ?? id;
        latest.delete(newTextId);
        latest.set(newTextId, { id, newTextId, edit });
        break;
      }
      // Read whole below, in order.
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
  const live = new Set<Edit>(); // the replaces that change what a span reads, and new text's latest versions
  const changed = changedLines(spans, replaces);
  for (const span of spans) {
    const replace = replaces.get(span.id);
    const text = textOf(replace, span);
    const replaced = text !== span.text;
    // A line edit that changes any run is every run's reading, since the others may move with it.
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
  // Its words all taken out, new text is gone, as an emptied span isn't an edit.
  const newTexts = [...latest.values()].filter(({ edit }) => edit.text.trim() !== "");
  for (const insert of newTexts) onPage(insert.edit.page).newTexts.push(insert);
  for (const insert of newTexts) live.add(insert.edit);

  const pages = new Map<number, PageEdits>();
  for (const [page, edits] of [...byPage].sort(([a], [b]) => a - b)) {
    const was = previous.pages.get(page);
    pages.set(page, was !== undefined && samePage(was, edits) ? was : edits);
  }

  // Sent: every edit but a replace or new text a later one overrides, or one that puts back the original or takes it out.
  const overridable = (edit: Edit) => edit.kind === "replace" || edit.kind === "replace_runs" || edit.kind === "insert";
  const sent = entries.filter(({ edit }) => !overridable(edit) || live.has(edit));
  const edits = sent.map((entry) => entry.edit);
  return { spans: readings, pages, edits, ...pagesKept(doc.pages.length, { edits, previous }) };
}

/** Keep `edit` as each of its spans' last: a line edit it takes over a span of is gone from every span of that line. */
function keepLast(replaces: Map<string, Replace | ReplaceRuns>, edit: Replace | ReplaceRuns): void {
  const spanIds = spansOf(edit);
  for (const spanId of spanIds) forget(replaces, spanId);
  for (const spanId of spanIds) replaces.set(spanId, edit);
}

/** Forget the replace `spanId` is last in, from every span it's to. */
function forget(replaces: Map<string, Replace | ReplaceRuns>, spanId: string): void {
  const last = replaces.get(spanId);
  if (last === undefined) return;
  for (const each of spansOf(last)) replaces.delete(each);
}

/** What `span` reads after `replace`, its run's words for a line's: its own with none. */
function textOf(replace: Replace | ReplaceRuns | undefined, span: SpanInfo): string {
  if (replace === undefined) return span.text;
  if (replace.kind === "replace") return replace.text;
  return replace.runs.find((run) => partKey(run.span_id, run.start ?? 0) === span.id)?.text ?? span.text;
}

/** The line edits kept that change a run's words: one that reads as the page does is no edit. */
function changedLines(spans: readonly SpanInfo[], replaces: ReadonlyMap<string, Replace | ReplaceRuns>) {
  const changed = new Set<ReplaceRuns>();
  for (const span of spans) {
    const replace = replaces.get(span.id);
    if (replace?.kind === "replace_runs" && textOf(replace, span) !== span.text) changed.add(replace);
  }
  return changed;
}

/** The pages a download keeps after `edits`, and those it leaves out: `previous`'s own while they're the same. */
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
