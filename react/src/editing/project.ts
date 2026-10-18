// What the document reads as with its edits made: the one reading of the history that what's
// drawn, what's rendered and what's exported all agree on. An object nothing changed stays
// the same object from one reading to the next, so whatever draws it can skip it.
import type { Edit, Insert, Replace, SpanInfo, Strategy } from "../api/types";
import type { Entry } from "./history";

export interface SpanReading {
  readonly span: SpanInfo;
  readonly text: string; // what it reads now
  readonly replaced: boolean; // it reads other than the original
  readonly strategy: Strategy | undefined; // how a replacement too long is to fit, when one is asked for
  readonly redacted: boolean; // to be taken out of the file
}

export interface InsertReading {
  readonly id: number; // its edit's, in the history
  readonly edit: Insert;
}

/** One page's edits. */
export interface PageEdits {
  readonly spans: readonly SpanReading[]; // in the document's order
  readonly inserts: readonly InsertReading[]; // in the order made
}

export interface Reading {
  readonly spans: ReadonlyMap<string, SpanReading>; // only the spans an edit changes
  readonly pages: ReadonlyMap<number, PageEdits>; // only the pages with an edit
  readonly edits: readonly Edit[]; // what the server is sent: only what changes the file
}

export const UNEDITED: Reading = { spans: new Map(), pages: new Map(), edits: [] };

/** What `span` reads in `reading`: its edit's words, else the document's own. */
export function textIn(reading: Reading, span: SpanInfo): string {
  return reading.spans.get(span.id)?.text ?? span.text;
}

/** Whether two readings of a span draw the same. */
export function sameSpan(a: SpanReading | undefined, b: SpanReading | undefined): boolean {
  if (a === b) return true;
  if (a === undefined || b === undefined) return false;
  return a.text === b.text && a.redacted === b.redacted && a.strategy === b.strategy;
}

/** Whether a page's edits draw the same in two readings. */
export function samePage(a: PageEdits | undefined, b: PageEdits | undefined): boolean {
  if (a === b) return true;
  if (a === undefined || b === undefined) return false;
  return (
    a.spans.length === b.spans.length &&
    a.spans.every((span, index) => sameSpan(span, b.spans[index])) &&
    a.inserts.length === b.inserts.length &&
    a.inserts.every((insert, index) => insert.id === b.inserts[index]?.id)
  );
}

/** A span that reads differently than its page's strips show: what it reads now, or that it's redacted. */
export interface Differing {
  readonly span: SpanInfo;
  readonly text: string;
  readonly redacted: boolean;
  readonly strategy: Strategy | undefined; // how a replacement too long is to fit, when one is asked for
}

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
    out.push({ span: reading.span, text: reading.span.text, redacted: false, strategy: undefined });
  }
  return out;
}

/**
 * The document's spans with `entries` made, in the order made. `previous` is the last reading:
 * whatever reads the same is taken from it as it was.
 */
export function project(spans: readonly SpanInfo[], entries: readonly Entry[], previous = UNEDITED): Reading {
  // The last replace to a span wins, as on the server; a redaction is for good.
  const replaces = new Map<string, Replace>();
  const redacted = new Set<string>();
  const inserts: InsertReading[] = [];
  for (const { id, edit } of entries) {
    switch (edit.kind) {
      case "replace":
        replaces.set(edit.span_id, edit);
        break;
      case "redact":
        redacted.add(edit.span_id);
        break;
      case "insert":
        inserts.push({ id, edit });
        break;
      default:
        edit satisfies never;
    }
  }

  const readings = new Map<string, SpanReading>();
  const byPage = new Map<number, { spans: SpanReading[]; inserts: InsertReading[] }>();
  const onPage = (page: number) => {
    const edits = byPage.get(page) ?? { spans: [], inserts: [] };
    byPage.set(page, edits);
    return edits;
  };
  const live = new Set<Edit>(); // the replaces that change what a span reads
  for (const span of spans) {
    const replace = replaces.get(span.id);
    const text = replace?.text ?? span.text;
    const replaced = text !== span.text;
    if (replaced && replace !== undefined) live.add(replace);
    if (!replaced && !redacted.has(span.id)) continue;
    const strategy = replaced ? replace?.strategy : undefined;
    const reading: SpanReading = { span, text, replaced, strategy, redacted: redacted.has(span.id) };
    const was = previous.spans.get(span.id);
    const kept = was !== undefined && sameSpan(was, reading) ? was : reading;
    readings.set(span.id, kept);
    onPage(span.page).spans.push(kept);
  }
  for (const insert of inserts) onPage(insert.edit.page).inserts.push(insert);

  const pages = new Map<number, PageEdits>();
  for (const [page, edits] of [...byPage].sort(([a], [b]) => a - b)) {
    const was = previous.pages.get(page);
    pages.set(page, was !== undefined && samePage(was, edits) ? was : edits);
  }

  // Sent: every edit but a replace a later one overrides, or one that puts back the original.
  const sent = entries.filter(({ edit }) => edit.kind !== "replace" || live.has(edit));
  return { spans: readings, pages, edits: sent.map((entry) => entry.edit) };
}
