// One open document: its state, and what every action works through. Each action is a
// function of the editor in the module named for it (typing, export); this one holds the
// state and the change every edit, undo and put-back goes through.
import { ProblemError } from "../api/client";
import type { Document, FontInfo, SpanInfo } from "../api/types";
import { Reopener } from "../documents/reopen";
import { EMPTY_HISTORY, entriesOf, type History, type HistoryAction, historyReducer, touching } from "./history";
import { type EditedView, project, UNEDITED } from "./project";
import { type Drawn, NOTHING_DRAWN, RenderQueue } from "./render";
import { createStore, type Store } from "./store";

/** The text being typed into a span, not yet in the history. */
export interface Draft {
  readonly spanId: string;
  readonly page: number;
  readonly atPt: number | null; // where in the span the press was, from its start: the word to select
  readonly text: string;
}

/** A line under the bar about what just happened. */
export interface Notice {
  readonly tone: "plain" | "warn";
  readonly text: string;
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
  readonly view: EditedView; // what the history reads as
  readonly drawn: Drawn; // the server's strips, and what they were drawn from
  readonly draft: Draft | null;
  readonly returnedTo: string | null; // the span focus went back to after its edit: its note stays shut
  readonly notice: Notice | null;
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
  const [first] = opened.notices;
  const store = createStore<EditorState>({
    doc: opened,
    layout: layoutOf(opened),
    scale,
    history: EMPTY_HISTORY,
    view: UNEDITED,
    drawn: NOTHING_DRAWN,
    draft: null,
    returnedTo: null,
    notice: first === undefined ? null : { tone: "warn", text: first.detail },
    said: "",
    exporting: false,
  });
  // The hour ran out and the document opened again: the same spans, under a new id.
  const reopener = new Reopener(file, opened, (doc) =>
    store.set({ doc, layout: layoutOf(doc), notice: { tone: "plain", text: doc.copy.reopened } }),
  );
  const queue = new RenderQueue({
    reopener,
    scale,
    drawn: (drawn) => store.set({ drawn }),
    failed: (detail) => store.set({ notice: { tone: "warn", text: detail } }),
  });
  return { store, file, reopener, queue };
}

/** Every change to the history comes through here, and redraws what it changed. */
export function change(editor: Editor, action: HistoryAction): void {
  const { store, queue } = editor;
  const state = store.get();
  const history = historyReducer(state.history, action);
  if (history === state.history) return;
  const view = project(state.doc.spans, entriesOf(history), state.view);
  // A message about the last export or reopening is stale once the user edits again.
  const notice = state.notice?.tone === "plain" ? null : state.notice;
  store.set({ history, view, notice });
  queue.draw(view);
}

/** Put a span back as the document had it, from its margin note. */
export function putBack(editor: Editor, spanId: string): void {
  const { store } = editor;
  change(editor, { kind: "remove", ids: touching(store.get().history, spanId) });
  store.set({ said: `Put back ${store.get().layout.spans.get(spanId)?.text ?? ""}` });
}

/** Tell a screen reader, for what the page doesn't show. */
export function say(editor: Editor, text: string): void {
  editor.store.set({ said: text });
}

/** A page image failed: the document may have gone. */
export function imageFailed(editor: Editor): void {
  editor.reopener.check().catch((error: unknown) => {
    if (!(error instanceof ProblemError)) throw error;
    editor.store.set({ notice: { tone: "warn", text: error.problem.detail } });
  });
}

/** How many spans read other than the original. */
export function changedCount(state: EditorState): number {
  return state.view.spans.size;
}

/** How many changed spans are drawn in a similar font, not the file's own. */
export function similarCount(state: EditorState): number {
  let count = 0;
  for (const { span, replaced } of state.view.spans.values()) {
    const inSimilar = state.layout.fonts.get(span.font)?.substitute != null;
    const missing = state.drawn.fits.get(span.page)?.[span.id]?.missing ?? [];
    if (replaced && (inSimilar || missing.length > 0)) count++;
  }
  return count;
}
