// One open document: its state, and what every action works through. Each action is a
// function of the editor in the module named for it (typing, export); this one holds the
// state and the change every edit, undo and put-back goes through.
import { ProblemError } from "../api/client";
import type { Document, FontInfo, SpanInfo } from "../api/types";
import { reportBug } from "../bugs";
import { Reopener } from "../documents/reopen";
import { EMPTY_HISTORY, entriesOf, type History, type HistoryAction, historyReducer, touching } from "./history";
import { NO_NOTICES, type Notices, plain, warn } from "./notices";
import { project, type Reading, UNEDITED } from "./project";
import { type Drawn, NOTHING_DRAWN, RenderQueue } from "./render";
import { createStore, type Store } from "./store";

/** The text being typed into a span, not yet in the history. */
export interface Draft {
  readonly spanId: string;
  readonly page: number;
  readonly atPt: number | null; // where in the span the press was, from its start: the word to select
  readonly text: string;
}

/**
 * A span whose mark takes focus as soon as it's drawn: back from its field, or put back from its
 * margin note. Its note stays shut until focus moves on, since it would cover the words.
 */
export interface FocusTo {
  readonly spanId: string;
}

/** The document's spans and fonts, looked up by what the page needs. */
export interface Layout {
  readonly spans: ReadonlyMap<string, SpanInfo>;
  readonly pages: ReadonlyMap<number, readonly SpanInfo[]>;
  readonly fonts: ReadonlyMap<string, FontInfo>;
}

export interface EditorState {
  readonly doc: Document; // the server's copy now
  readonly layout: Layout;
  readonly scale: number; // pixels per point, as the page images are drawn
  readonly history: History;
  readonly reading: Reading; // what the history reads as
  readonly drawn: Drawn; // the server's strips, and what they were drawn from
  readonly draft: Draft | null;
  readonly focusTo: FocusTo | null;
  readonly notices: Notices; // the lines under the bar, but the render's, which are in `drawn`
  readonly said: string; // what a screen reader hears, for what the page doesn't show
  readonly exporting: boolean;
}

export interface Editor {
  readonly store: Store<EditorState>;
  readonly file: File;
  readonly reopener: Reopener;
  readonly queue: RenderQueue;
}

export function layoutOf(doc: Document): Layout {
  const pages = new Map<number, SpanInfo[]>();
  for (const span of doc.spans) {
    const onPage = pages.get(span.page);
    if (onPage === undefined) pages.set(span.page, [span]);
    else onPage.push(span);
  }
  return {
    spans: new Map(doc.spans.map((span) => [span.id, span])),
    pages,
    fonts: new Map(doc.fonts.map((font) => [font.name, font])),
  };
}

export function createEditor(file: File, opened: Document, scale: number): Editor {
  const store = createStore<EditorState>({
    doc: opened,
    layout: layoutOf(opened),
    scale,
    history: EMPTY_HISTORY,
    reading: UNEDITED,
    drawn: NOTHING_DRAWN,
    draft: null,
    focusTo: null,
    notices: { ...NO_NOTICES, document: opened.notices.map((notice) => warn(notice.detail)) },
    said: "",
    exporting: false,
  });
  // The server no longer had the document, and it opened again: the same spans, under a new id.
  const reopener = new Reopener(file, opened, (doc) =>
    store.set({ doc, layout: layoutOf(doc), notices: { ...store.get().notices, reopen: plain(doc.copy.reopened) } }),
  );
  const queue = new RenderQueue({ reopener, scale, drawn: (drawn) => store.set({ drawn }) });
  return { store, file, reopener, queue };
}

/**
 * Every change to the history comes through here, and redraws what it changed. `also` is the
 * rest of the action that made it, so what's drawn never sees one half without the other.
 */
export function change(editor: Editor, action: HistoryAction, also: Partial<EditorState> = {}): void {
  const { store, queue } = editor;
  const state = store.get();
  const history = historyReducer(state.history, action);
  if (history === state.history) {
    if (Object.keys(also).length > 0) store.set(also);
    return;
  }
  const reading = project(state.doc.spans, entriesOf(history), state.reading);
  // What the last export or reopening said is stale once the user edits again.
  const notices = { ...state.notices, export: null, reopen: null };
  store.set({ history, reading, notices, ...also });
  queue.draw(reading);
}

/** Put a span back as the document had it, from its margin note; focus goes to the span. */
export function putBack(editor: Editor, spanId: string): void {
  const { history, layout } = editor.store.get();
  change(
    editor,
    { kind: "remove", ids: touching(history, spanId) },
    { said: `Put back ${layout.spans.get(spanId)?.text ?? ""}`, focusTo: { spanId } },
  );
}

/** Focus has left the span it was sent to: the next visit is an ordinary one. */
export function focusMoved(editor: Editor): void {
  editor.store.set({ focusTo: null });
}

/** The editor is going: a render in flight is dropped rather than landing on nothing. */
export function closeEditor(editor: Editor): void {
  editor.queue.stop();
}

/** Tell a screen reader, for what the page doesn't show. */
export function say(editor: Editor, text: string): void {
  editor.store.set({ said: text });
}

/** A page image failed: the document may have gone. */
export function imageFailed(editor: Editor): void {
  editor.reopener.check().catch((error: unknown) => {
    const text = error instanceof ProblemError ? error.problem.detail : reportBug(error);
    const { notices } = editor.store.get();
    editor.store.set({ notices: { ...notices, reopen: warn(text) } });
  });
}

/** How many spans read other than the original. */
export function changedCount(state: EditorState): number {
  return state.reading.spans.size;
}

/** How many changed spans are drawn in a substitute (the reader's "similar font"), not the file's own. */
export function substitutedCount(state: EditorState): number {
  let count = 0;
  for (const { span, replaced } of state.reading.spans.values()) {
    const inSubstitute = state.layout.fonts.get(span.font)?.substitute != null;
    const missing = state.drawn.fits.get(span.page)?.[span.id]?.missing ?? [];
    if (replaced && (inSubstitute || missing.length > 0)) count++;
  }
  return count;
}
