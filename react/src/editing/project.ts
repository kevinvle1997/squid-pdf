// The one reading of the history that what's drawn, rendered and exported agree on. An object nothing
// changed stays the same object from one reading to the next, so whatever draws it can skip it.
import type { Document, Edit, Insert, PageInfo, Redact, Replace, SpanInfo, Strategy } from "../api/types";
import { type Entry, newTextIdsOf } from "./history";

/** A range of a span's text by code point, as the server counts it: what a redaction names. */
export type CodePoints = NonNullable<Redact["range"]>;

/** A run of a span's redacted words, from `start` to `end` in the field's units: UTF-16, as JavaScript counts. */
export interface RedactedWords {
  readonly start: number;
  readonly end: number;
}

/** What redactions take of a span: all of it, or runs of its words, in order, none meeting another. */
export type Redacted = "whole" | readonly RedactedWords[];

export interface SpanReading {
  readonly span: SpanInfo;
  readonly text: string; // what it reads now
  readonly replaced: boolean; // it reads other than the original
  readonly strategy: Strategy | undefined; // how a replacement too long is to fit, when one is asked for
  readonly redacted: Redacted | null; // what's to be taken out of the file, if anything
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
  return a.text === b.text && sameRedacted(a.redacted, b.redacted) && a.strategy === b.strategy;
}

/** Whether two redactions of a span take the same: all of it, or the same runs of words. */
function sameRedacted(a: Redacted | null, b: Redacted | null): boolean {
  if (typeof a !== "object" || typeof b !== "object" || a === null || b === null) return a === b;
  return a.length === b.length && a.every((run, index) => run.start === b[index]?.start && run.end === b[index]?.end);
}

/** The field's units of `text` that `range` takes: by code point, as the server counts, from `start` to `end`. */
export function unitsOf(text: string, [start, end]: CodePoints): RedactedWords {
  const letters = [...text];
  return { start: letters.slice(0, start).join("").length, end: letters.slice(0, end).join("").length };
}

/** The runs of words redacted of a span as it reads: none when it's redacted whole, or not at all. */
export function redactedWordsOf(reading: SpanReading | undefined): readonly RedactedWords[] {
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

/** What `redactions` of `span` take, in the order its text reads: all of it, or its runs merged; null with none. */
function redactedBy(span: SpanInfo, redactions: readonly Redact[] | undefined): Redacted | null {
  if (redactions === undefined) return null;
  const runs: RedactedWords[] = [];
  for (const { range } of redactions) {
    // One redaction of all of it takes in the rest.
    if (range == null) return "whole";
    runs.push(unitsOf(span.text, range));
  }
  const merged = mergedWords(runs);
  const all = merged.length === 1 && merged[0]?.start === 0 && merged[0]?.end === span.text.length;
  return all ? "whole" : merged;
}

/**
 * `words` in the text's order, each run of them that overlap or meet as one, as the server merges them:
 * `tests/editing/redacted_words_cases.json` holds both to it.
 */
function mergedWords(words: readonly RedactedWords[]): RedactedWords[] {
  const runs: RedactedWords[] = [];
  for (const run of [...words].sort((a, b) => a.start - b.start)) {
    const last = runs.at(-1);
    const meetsLast = last !== undefined && run.start <= last.end;
    if (meetsLast) {
      runs[runs.length - 1] = { ...last, end: Math.max(last.end, run.end) };
      continue;
    }
    runs.push(run);
  }
  return runs;
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
export type Differing = Pick<SpanReading, "span" | "text" | "redacted" | "strategy">;

/**
 * The spans that read differently in `now` than in `then`, with what each reads now: the
 * original's words where `now` has put it back.
 */
export function differing(now: PageEdits | undefined, then: PageEdits | undefined): Differing[] {
  const was = new Map(then?.spans.map((reading) => [reading.span.id, reading]));
  const out: Differing[] = [];
  for (const reading of now?.spans ?? []) {
    const { span, text, redacted, strategy } = reading;
    if (!sameSpan(reading, was.get(span.id))) out.push({ span, text, redacted, strategy });
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
  const replaces = new Map<string, Replace>();
  const redactions = new Map<string, Redact[]>();
  // New text's latest version wins, as a span's last replace does: by its first version's id, in the order last made.
  const latest = new Map<number, NewTextReading>();
  const newTextIds = newTextIdsOf(entries);
  for (const { id, edit } of entries) {
    switch (edit.kind) {
      case "replace":
        replaces.set(edit.span_id, edit);
        break;
      case "redact":
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
  for (const span of spans) {
    const replace = replaces.get(span.id);
    const text = replace?.text ?? span.text;
    const replaced = text !== span.text;
    if (replaced && replace !== undefined) live.add(replace);
    const redacted = redactedBy(span, redactions.get(span.id));
    if (!replaced && redacted === null) continue;
    const strategy = replaced ? replace?.strategy : undefined;
    const reading: SpanReading = { span, text, replaced, strategy, redacted };
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
  const overridable = (edit: Edit) => edit.kind === "replace" || edit.kind === "insert";
  const sent = entries.filter(({ edit }) => !overridable(edit) || live.has(edit));
  const edits = sent.map((entry) => entry.edit);
  return { spans: readings, pages, edits, ...pagesKept(doc.pages.length, { edits, previous }) };
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
