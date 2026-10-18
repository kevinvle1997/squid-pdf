// One open document: its state, and what every action works through. Each action is a
// function of the editor in the module named for it (typing, export); this one holds the
// state and the change every edit, undo and put-back goes through.
import type { Document, Edit, FontInfo, SpanInfo, Strategy } from "../api/types";
import { Reopener } from "../documents/reopen";
import type { ExportAhead } from "./export";
import { FIND_CLOSED, type FindState, findAfterChange } from "./find";
import { reattach, shownNameOf } from "./fonts";
import {
  EMPTY_HISTORY,
  entriesOf,
  type History,
  type HistoryAction,
  historyReducer,
  type Step,
  spanOf,
  touching,
} from "./history";
import type { PageImage } from "./images";
import { type Keeping, keepUntil, keepWork } from "./keeping";
import { wontMatch } from "./marks";
import { NO_NOTICES, type Notices, newlySaid, noticeLines, noticesAfterChange, plain, warn } from "./notices";
import { project, type Reading, textIn, UNEDITED } from "./project";
import { type Drawn, NOTHING_DRAWN, RenderQueue } from "./render";
import { NOTHING_SETTLING, type Settling, settlingAfter } from "./settle";
import { createStore, type Store } from "./store";

/** The text being typed into a span, not yet in the history. */
export interface Draft {
  readonly spanId: string;
  readonly page: number;
  readonly atPt: number | null; // where in the span the press was, from its start: the word to select
  readonly text: string;
  readonly strategy: Strategy | undefined; // the way out of a long line chosen, kept while it's offered
}

/**
 * A span whose mark takes focus as soon as it's drawn: back from its field, or put back from its
 * margin note. Its note stays shut until focus moves on, since it would cover the words.
 */
export interface FocusTo {
  readonly spanId: string;
}

/** A redacted span the user asked to edit: it asks first, in place, before the redaction goes. */
export interface Unredacting {
  readonly spanId: string;
  readonly atPt: number | null; // where in the span the press was: the word to select, if it's edited
}

/** Words for a screen reader, counted: the same words said again are heard again. */
export interface Spoken {
  readonly text: string;
  readonly count: number;
}

/** A span its page brings into view, if it's off screen: what undo or redo changed. Counted, as `Spoken` is. */
export interface ScrollTo {
  readonly spanId: string;
  readonly count: number;
}

// The last count given, for the whole page: two editors' counts never meet, so one will do.
let counted = 0;

/** `text`, to be said now. */
export function spoken(text: string): Spoken {
  counted += 1;
  return { text, count: counted };
}

function scrollingTo(spanId: string): ScrollTo {
  counted += 1;
  return { spanId, count: counted };
}

/** What's worked out once from the document: its spans and fonts, looked up by what the page needs. */
export interface Layout {
  readonly spans: ReadonlyMap<string, SpanInfo>;
  readonly pages: ReadonlyMap<number, readonly SpanInfo[]>;
  readonly fonts: ReadonlyMap<string, FontInfo>;
  readonly warnedFonts: ReadonlySet<string>; // by name shown: the fonts an edit warns of, a similar font or no match
  readonly unlikeAtRest: UnlikeAtRest; // the bar reads it on every change, so it's one object until the document changes
}

/**
 * Before any change, how many places an edit won't match: drawn in a similar font, or in the file's
 * own but unlike the text around it (`approximate`). The bar says it, as the mockup's status does.
 */
export interface UnlikeAtRest {
  readonly count: number;
  readonly approximate: boolean;
}

export interface EditorState {
  readonly doc: Document; // the server's copy now
  readonly layout: Layout;
  readonly scale: number; // pixels per point, as the page images are drawn
  readonly history: History;
  readonly reading: Reading; // what the history reads as
  readonly drawn: Drawn; // the server's strips, and what they were drawn from
  readonly settling: ReadonlyMap<number, Settling>; // by page: the spans the last render there disagreed with
  readonly draft: Draft | null;
  readonly unredacting: Unredacting | null;
  readonly focusTo: FocusTo | null;
  readonly notices: Notices; // the lines under the bar, but the render's, which are in `drawn`
  readonly said: Spoken; // what a screen reader hears, for what the page doesn't show
  readonly scrollTo: ScrollTo | null;
  readonly exporting: boolean;
  readonly exported: readonly Edit[]; // the edits the last export that worked sent: none before one
  readonly attaching: string | null; // the font whose copy is being added or removed, one at a time
  readonly focusFont: string | null; // the font whose button in the fonts list takes focus once drawn
  readonly quietFonts: ReadonlySet<string>; // the fonts whose warning an edit no longer shows in full
  readonly images: ReadonlyMap<number, PageImage>; // by page: each image that failed, until it loads
  readonly find: FindState;
}

export interface Editor {
  readonly store: Store<EditorState>;
  readonly file: File;
  readonly attached: Map<string, Blob>; // the user's copies of its fonts, beside the PDF, by font
  readonly reopener: Reopener;
  readonly queue: RenderQueue;
  readonly near: Set<number>; // the pages near the screen, as each one's sheet finds: drawn first
  readonly retries: Map<number, ReturnType<typeof setTimeout>>; // by page: when its failed image is asked for again
  readonly finding: { timer?: ReturnType<typeof setTimeout> }; // when what's typed in "Find" is found
  readonly ahead: ExportAhead; // the export started on the way to pressing Export
  readonly keeping: Keeping; // what's kept of it in this browser
}

export function layoutOf(doc: Document): Layout {
  const pages = new Map<number, SpanInfo[]>();
  for (const span of doc.spans) {
    const onPage = pages.get(span.page);
    if (onPage === undefined) pages.set(span.page, [span]);
    else onPage.push(span);
  }
  const fonts = new Map(doc.fonts.map((font) => [font.name, font]));
  const unlike = doc.spans.filter(wontMatch);
  return {
    spans: new Map(doc.spans.map((span) => [span.id, span])),
    pages,
    fonts,
    warnedFonts: new Set(unlike.map((span) => shownNameOf({ fonts }, span.font))),
    unlikeAtRest: { count: unlike.length, approximate: unlike.some((span) => span.why !== null) },
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
    settling: NOTHING_SETTLING,
    draft: null,
    unredacting: null,
    focusTo: null,
    notices: { ...NO_NOTICES, document: opened.notices.map((notice) => warn(notice.detail)) },
    said: { text: "", count: 0 },
    scrollTo: null,
    exporting: false,
    exported: UNEDITED.edits,
    attaching: null,
    focusFont: null,
    quietFonts: new Set(),
    images: new Map(),
    find: FIND_CLOSED,
  });
  // The server no longer had the document, and it opened again: the same spans, under a new id,
  // with the user's copies back before anything is drawn or exported from it.
  const reopener = new Reopener(file, opened, async (doc) => {
    const { judged, refusal } = await reattach(editor, doc);
    const reopen = plain(doc.copy.reopened);
    const notices = { ...store.get().notices, reopen, ...(refusal !== null && { font: refusal }) };
    const heard = refusal === null ? reopen.text : `${reopen.text} ${refusal.text}`;
    store.set({ doc: judged, layout: layoutOf(judged), notices, said: spoken(heard) });
    // Pages drawn with a copy now refused no longer show what export would give.
    if (refusal !== null) queue.redraw(store.get().reading);
    return judged;
  });
  const near = new Set<number>();
  const queue = new RenderQueue({
    reopener,
    scale,
    near,
    drawn: (drawn) => drawnNow(store, drawn),
    extended: (expiresAt) => keepUntil(editor, expiresAt),
  });
  const editor: Editor = {
    store,
    file,
    attached: new Map(),
    reopener,
    queue,
    near,
    retries: new Map(),
    finding: {},
    ahead: {},
    keeping: { file: false, history: null, until: Date.parse(opened.expires_at) },
  };
  return editor;
}

/**
 * A render landed or failed: what it drew goes in, where it disagrees with the preview pulses,
 * and a line it adds under the bar is heard.
 */
function drawnNow(store: Store<EditorState>, drawn: Drawn): void {
  const { notices, drawn: before, layout, doc, settling } = store.get();
  const heard = newlySaid(noticeLines(notices, before), noticeLines(notices, drawn));
  const settles = settlingAfter(before, drawn, { fonts: layout.fonts, rules: doc.fit, settling });
  store.set({ drawn, settling: settles, ...(heard !== "" && { said: spoken(heard) }) });
}

/** The editor is on screen: what opening the file found is heard, now its live region is there to say it. */
export function sayOpening(editor: Editor): void {
  const found = editor.store.get().notices.document;
  if (found.length > 0) say(editor, found.map((notice) => notice.text).join(" "));
}

/** The rest of an action, or what it is once the history's read: what undo says the span reads. */
type Also = Partial<EditorState> | ((reading: Reading) => Partial<EditorState>);

/**
 * Every change to the history comes through here, and redraws what it changed. `also` is the
 * rest of the action that made it, so what's drawn never sees one half without the other.
 */
export function change(editor: Editor, action: HistoryAction, also: Also = {}): void {
  const { store, queue } = editor;
  const state = store.get();
  const history = historyReducer(state.history, action);
  if (history === state.history) {
    if (typeof also !== "function" && Object.keys(also).length > 0) store.set(also);
    return;
  }
  const reading = project(state.doc.spans, entriesOf(history), state.reading);
  const notices = noticesAfterChange(state.notices);
  const find = findAfterChange(state, reading);
  store.set({ history, reading, notices, find, ...(typeof also === "function" ? also(reading) : also) });
  queue.draw(reading);
  void keepWork(editor);
}

/** Put a span back as the document had it, from its margin note; focus goes to the span. */
export function putBack(editor: Editor, spanId: string): void {
  const { history, layout } = editor.store.get();
  change(
    editor,
    { kind: "remove", ids: touching(history, spanId) },
    { said: spoken(`Put back ${layout.spans.get(spanId)?.text ?? ""}`), focusTo: { spanId } },
  );
}

/** Take off the last thing done; say what its span reads now and bring it into view. Focus stays. */
export function undo(editor: Editor): void {
  const step = editor.store.get().history.done.at(-1);
  stepped(editor, { kind: "undo" }, { step, words: "Back to" });
}

/** Bring back the last thing undone, said and brought into view as undo does. */
export function redo(editor: Editor): void {
  const step = editor.store.get().history.undone.at(-1);
  stepped(editor, { kind: "redo" }, { step, words: "Changed to" });
}

function stepped(editor: Editor, action: HistoryAction, { step, words }: { step: Step | undefined; words: string }) {
  const spanId = step?.map((entry) => spanOf(entry.edit)).find((id) => id !== undefined);
  if (step === undefined || spanId === undefined) {
    change(editor, action);
    return;
  }
  const span = editor.store.get().layout.spans.get(spanId);
  change(editor, action, (reading) => ({
    said: spoken(`${words} ${span === undefined ? "" : textIn(reading, span)}`),
    scrollTo: scrollingTo(spanId),
  }));
}

/** Focus has left the span it was sent to: the next visit is an ordinary one. */
export function focusMoved(editor: Editor): void {
  editor.store.set({ focusTo: null });
}

/** The editor is going: a render in flight, and any image still to be asked for, are dropped rather than landing on nothing. */
export function closeEditor(editor: Editor): void {
  editor.queue.stop();
  for (const retry of editor.retries.values()) clearTimeout(retry);
  editor.retries.clear();
  clearTimeout(editor.finding.timer);
  clearTimeout(editor.ahead.timer);
  editor.ahead.started?.ask.abort();
}

/** Tell a screen reader, for what the page doesn't show. */
export function say(editor: Editor, text: string): void {
  editor.store.set({ said: spoken(text) });
}

/** Whether the edits, or words still being typed, differ from what the last export took: leaving would lose them. */
export function unexported(state: EditorState): boolean {
  const { draft, layout, exported, reading } = state;
  const span = draft === null ? undefined : layout.spans.get(draft.spanId);
  const reads = span === undefined ? undefined : textIn(reading, span);
  // Closing the tab ends no typing: what's in the field never reaches the history.
  if (draft !== null && draft.text !== reads) return true;
  const { edits } = reading;
  return edits.length !== exported.length || edits.some((edit, index) => edit !== exported[index]);
}

/** How many spans read other than the original. */
export function changedCount(state: EditorState): number {
  return state.reading.spans.size;
}

/**
 * How many changed spans won't match: drawn in a substitute (the reader's "similar font"), or in
 * their own font but unlike the text around them. Once one is the latter, all "will look different".
 */
export function warnedCount(state: EditorState): { count: number; label: string } {
  let count = 0;
  let approximate = false;
  for (const { span, replaced } of state.reading.spans.values()) {
    if (!replaced) continue;
    if (span.why !== null) approximate = true;
    const missing = state.drawn.fits.get(span.page)?.[span.id]?.missing ?? [];
    if (wontMatch(span) || missing.length > 0) count++;
  }
  return { count, label: approximate ? "will look different" : "in a similar font" };
}
