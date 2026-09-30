// What the document reads as with its edits made: the one reading of the history that what's
// drawn, what's rendered and what's exported all agree on. An object nothing changed stays
// the same object from one reading to the next, so whatever draws it can skip it.
import type { Edit, Insert, SpanInfo, Strategy } from "../api/types";
import type { Entry } from "./history";

export interface SpanView {
  readonly span: SpanInfo;
  readonly text: string; // what it reads now
  readonly replaced: boolean; // it reads other than the original
  readonly strategy: Strategy | undefined; // how a replacement too long is to fit, when one is asked for
  readonly redacted: boolean; // to be taken out of the file
}

export interface InsertView {
  readonly id: number; // its edit's, in the history
  readonly edit: Insert;
}

/** One page's edits. */
export interface PageEdits {
  readonly spans: readonly SpanView[]; // in the document's order
  readonly inserts: readonly InsertView[]; // in the order made
}

export interface EditedView {
  readonly spans: ReadonlyMap<string, SpanView>; // only the spans an edit changes
  readonly pages: ReadonlyMap<number, PageEdits>; // only the pages with an edit
  readonly edits: readonly Edit[]; // what the server is sent: only what changes the file
  readonly ids: readonly number[]; // the history's id for each edit sent, for what the server says of it
}

export const UNEDITED: EditedView = { spans: new Map(), pages: new Map(), edits: [], ids: [] };

/** Whether two readings of a span draw the same. */
export function sameSpan(a: SpanView | undefined, b: SpanView | undefined): boolean {
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

/**
 * The spans that read differently in `now` than in `then`, with what each reads now: the
 * original's words where `now` has put it back.
 */
export function differing(now: PageEdits | undefined, then: PageEdits | undefined): { span: SpanInfo; text: string }[] {
  const was = new Map(then?.spans.map((view) => [view.span.id, view]));
  const out: { span: SpanInfo; text: string }[] = [];
  for (const view of now?.spans ?? []) {
    if (!sameSpan(view, was.get(view.span.id))) out.push({ span: view.span, text: view.text });
    was.delete(view.span.id);
  }
  for (const view of was.values()) out.push({ span: view.span, text: view.span.text });
  return out;
}

/**
 * The document's spans with `entries` made, in the order made. `previous` is the last reading:
 * whatever reads the same is taken from it as it was.
 */
export function project(spans: readonly SpanInfo[], entries: readonly Entry[], previous = UNEDITED): EditedView {
  // The last replace to a span wins, as on the server; a redaction is for good.
  const replaces = new Map<string, Edit>();
  const redacted = new Set<string>();
  const inserts: InsertView[] = [];
  for (const { id, edit } of entries) {
    if (edit.kind === "replace") replaces.set(edit.span_id, edit);
    else if (edit.kind === "redact") redacted.add(edit.span_id);
    else inserts.push({ id, edit });
  }

  const views = new Map<string, SpanView>();
  const byPage = new Map<number, { spans: SpanView[]; inserts: InsertView[] }>();
  const onPage = (page: number) => {
    let edits = byPage.get(page);
    if (edits === undefined) byPage.set(page, (edits = { spans: [], inserts: [] }));
    return edits;
  };
  const live = new Set<Edit>(); // the replaces that change what a span reads
  for (const span of spans) {
    const replace = replaces.get(span.id);
    const text = replace?.kind === "replace" ? replace.text : span.text;
    const replaced = text !== span.text;
    if (replaced && replace !== undefined) live.add(replace);
    if (!replaced && !redacted.has(span.id)) continue;
    const strategy = replaced && replace?.kind === "replace" ? replace.strategy : undefined;
    const view: SpanView = { span, text, replaced, strategy, redacted: redacted.has(span.id) };
    const was = previous.spans.get(span.id);
    const kept = was !== undefined && sameSpan(was, view) ? was : view;
    views.set(span.id, kept);
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
  return { spans: views, pages, edits: sent.map((entry) => entry.edit), ids: sent.map((entry) => entry.id) };
}
