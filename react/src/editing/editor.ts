// One open document: its state, and the change every edit, undo and put-back goes through. Each action
// is a function of the editor in the module named for it (typing, export).
import type { Document, Edit, SpanInfo } from "../api/types";
import { Reopener } from "../documents/reopen";
import type { Press } from "./caret";
import type { ExportAhead } from "./export";
import { FIND_CLOSED, type FindState, findAfterChange } from "./find";
import { reattach } from "./fonts";
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
import type { FailedImage } from "./images";
import { type Mismatches, type MismatchPlace, mismatchesOf, placeOf } from "./mismatches";
import { heardIn, NO_NOTICES, type Notices, newlyHeard, noticeLines, noticesAfterChange, plain, warn } from "./notices";
import { disagreementsAfter, NO_DISAGREEMENTS, type PreviewDisagreement } from "./previewDisagreements";
import { project, type Reading, textIn, UNEDITED } from "./project";
import { type Drawn, NOTHING_DRAWN, RenderQueue } from "./render";
import { type FontsByObject, fontsByObject, shownNameIn } from "./spanFonts";
import { createStore, type Store } from "./store";
import type { Draft } from "./typing";
import { type Persistence, persistUntil, persistWork } from "./workPersistence";
import { FITTED, type Zoom } from "./zoom";

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
  readonly press: Press | null; // the press that asked: where the caret goes, if it's edited
}

/** Words for a screen reader, counted: the same words announced again are heard again. */
export interface Announcement {
  readonly text: string;
  readonly count: number;
}

/** A span its page brings into view, if it's off screen: what undo or redo changed, or the place find went to. Counted, as `Announcement` is. */
export interface ScrollTo {
  readonly spanId: string;
  readonly count: number;
  readonly block: ScrollLogicalPosition; // "nearest" for what changed in place; "center" for a place gone to
}

// The last count given, for the whole page: two editors' counts never meet, so one will do.
let lastCount = 0;

/** `text`, to be announced now. */
export function announcing(text: string): Announcement {
  lastCount += 1;
  return { text, count: lastCount };
}

/** `spanId`, to be brought into view now: as little as it takes, or into the middle (`block`). */
export function scrollingTo(spanId: string, block: ScrollLogicalPosition = "nearest"): ScrollTo {
  lastCount += 1;
  return { spanId, count: lastCount, block };
}

/** What's worked out once from the document: its spans and fonts, looked up by what the page needs. */
export interface Layout {
  readonly spans: ReadonlyMap<string, SpanInfo>;
  readonly pages: ReadonlyMap<number, readonly SpanInfo[]>;
  readonly fonts: FontsByObject;
  readonly warnedFonts: ReadonlySet<string>; // by name shown: the fonts an edit warns of, a similar font or no match
  readonly mismatchesAtRest: Mismatches; // before any change, grouped: one object until the document changes
}

export interface EditorState {
  readonly doc: Document; // the server's copy now
  readonly layout: Layout;
  readonly scale: number; // pixels per point, as the page images are drawn
  readonly zoom: Zoom; // how large the pages show
  readonly pageInView: number; // the page under the window's middle, as the bar says where the reader is
  readonly history: History;
  readonly reading: Reading; // what the history reads as
  readonly drawn: Drawn; // the server's strips, and what they were drawn from
  readonly disagreements: ReadonlyMap<number, PreviewDisagreement>; // by page: the spans the last render there disagreed with
  readonly draft: Draft | null;
  readonly unredacting: Unredacting | null;
  readonly focusTo: FocusTo | null;
  readonly notices: Notices; // the lines under the bar, and reopening in it; the render's lines are in `drawn`
  readonly announcement: Announcement; // what a screen reader hears, for what the page doesn't show
  readonly scrollTo: ScrollTo | null;
  readonly exporting: boolean;
  readonly exported: readonly Edit[]; // the edits the last export that worked sent: none before one
  readonly attaching: string | null; // the font whose copy is being added or removed, one at a time
  readonly focusFont: string | null; // the font whose button in the fonts list takes focus once drawn
  readonly shownMismatches: ReadonlySet<string>; // the rows of the mismatch list shown on the page, by key
  readonly mutedFonts: ReadonlySet<string>; // the fonts whose warning an edit no longer shows in full, by name shown
  readonly images: ReadonlyMap<number, FailedImage>; // by page: each image that failed, until it loads
  readonly find: FindState;
  readonly shortcutsOpen: boolean; // the list of the editor's keys, from the bar
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
  readonly persistence: Persistence; // what's persisted of it in this browser
}

export function layoutOf(doc: Document): Layout {
  const pages = new Map<number, SpanInfo[]>();
  for (const span of doc.spans) {
    const onPage = pages.get(span.page);
    if (onPage === undefined) pages.set(span.page, [span]);
    else onPage.push(span);
  }
  const fonts = fontsByObject(doc.fonts);
  const atRest = { changed: false, reading: undefined, missing: [] };
  const mismatches = doc.spans.flatMap((span) => placeOf(span, atRest) ?? []);
  return {
    spans: new Map(doc.spans.map((span) => [span.id, span])),
    pages,
    fonts,
    warnedFonts: new Set(mismatches.map(({ span }) => shownNameIn(fonts, span))),
    mismatchesAtRest: mismatchesOf(mismatches, { fonts, sentences: doc.sentences }),
  };
}

export function createEditor(file: File, opened: Document, scale: number): Editor {
  const store = createStore<EditorState>({
    doc: opened,
    layout: layoutOf(opened),
    scale,
    zoom: FITTED,
    pageInView: 0,
    history: EMPTY_HISTORY,
    reading: project(opened, []),
    drawn: NOTHING_DRAWN,
    disagreements: NO_DISAGREEMENTS,
    draft: null,
    unredacting: null,
    focusTo: null,
    notices: { ...NO_NOTICES, document: opened.notices.map((notice) => warn(notice.detail)) },
    announcement: { text: "", count: 0 },
    scrollTo: null,
    exporting: false,
    exported: UNEDITED.edits,
    attaching: null,
    focusFont: null,
    shownMismatches: new Set(),
    mutedFonts: new Set(),
    images: new Map(),
    find: FIND_CLOSED,
    shortcutsOpen: false,
  });
  const reopener = new Reopener(file, opened, (doc) => reopened(editor, doc));
  const near = new Set<number>();
  const queue = new RenderQueue({
    reopener,
    scale,
    near,
    drawn: (drawn) => drawnNow(store, drawn),
    extended: (expiresAt) => persistUntil(editor, expiresAt),
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
    persistence: { file: false, history: null, until: Date.parse(opened.expires_at) },
  };
  return editor;
}

/**
 * The server no longer had the document, and it opened again: the same spans, under a new id,
 * with the user's copies back before anything is drawn or exported from it.
 */
async function reopened(editor: Editor, doc: Document): Promise<Document> {
  const { store, queue } = editor;
  const { judged, refusal } = await reattach(editor, doc);
  const reopen = plain(doc.sentences.reopened);
  const notices = { ...store.get().notices, reopen, ...(refusal !== null && { font: refusal }) };
  const heard = refusal === null ? reopen.text : `${reopen.text} ${refusal.text}`;
  store.set({ doc: judged, layout: layoutOf(judged), notices, announcement: announcing(heard) });
  // Pages drawn with a copy now refused no longer show what export would give.
  if (refusal !== null) queue.redraw(store.get().reading);
  return judged;
}

/**
 * A render landed or failed: what it drew goes in, where it disagrees with the preview pulses,
 * and a line it adds under the bar is heard.
 */
function drawnNow(store: Store<EditorState>, drawn: Drawn): void {
  const { notices, drawn: before, layout, doc, disagreements } = store.get();
  const heard = newlyHeard(noticeLines(notices, before), noticeLines(notices, drawn));
  const after = disagreementsAfter(before, drawn, { fonts: layout.fonts, rules: doc.fit, disagreements });
  store.set({ drawn, disagreements: after, ...(heard !== "" && { announcement: announcing(heard) }) });
}

/** The editor is on screen: what opening the file found is heard, now its live region is there to announce it. */
export function announceOpening(editor: Editor): void {
  const found = editor.store.get().notices.document;
  if (found.length > 0) announce(editor, heardIn(found));
}

/** The rest of an action, or what it is once the change is made: what undo says the span reads. */
export type Also = Partial<EditorState> | ((next: EditorState) => Partial<EditorState>);

/** The state once `actions` are made, in turn: the history, what it reads as, and what clears with it. Nothing is set. */
export function changed(state: EditorState, actions: readonly HistoryAction[]): EditorState {
  const history = actions.reduce(historyReducer, state.history);
  if (history === state.history) return state;
  const reading = project(state.doc, entriesOf(history), state.reading);
  return {
    ...state,
    history,
    reading,
    notices: noticesAfterChange(state.notices),
    find: findAfterChange(state, reading),
  };
}

/**
 * Every change to the history comes through here, and redraws what it changed. `also` is the
 * rest of the action that made it, so what's drawn never sees one half without the other.
 */
export function change(editor: Editor, action: HistoryAction | readonly HistoryAction[], also: Also = {}): void {
  const { store, queue } = editor;
  const state = store.get();
  const next = changed(state, [action].flat());
  const rest = typeof also === "function" ? also(next) : also;
  if (next === state) {
    if (Object.keys(rest).length > 0) store.set(rest);
    return;
  }
  const { history, reading, notices, find } = next;
  store.set({ history, reading, notices, find, ...rest });
  queue.draw(reading);
  void persistWork(editor);
}

/** Put a span back as the document had it, from its margin note; focus goes to the span. */
export function putBack(editor: Editor, spanId: string): void {
  const { history, layout } = editor.store.get();
  change(
    editor,
    { kind: "remove", ids: touching(history, spanId) },
    { announcement: announcing(`Put back ${layout.spans.get(spanId)?.text ?? ""}`), focusTo: { spanId } },
  );
}

/** Take off the last thing done; announce what its span reads now and bring it into view. Focus stays. */
export function undo(editor: Editor): void {
  const step = editor.store.get().history.done.at(-1);
  stepped(editor, { kind: "undo" }, { step, words: "Back to" });
}

/** Bring back the last thing undone, announced and brought into view as undo does. */
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
  change(editor, action, ({ reading }) => ({
    announcement: announcing(`${words} ${span === undefined ? "" : textIn(reading, span)}`),
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
export function announce(editor: Editor, text: string): void {
  editor.store.set({ announcement: announcing(text) });
}

/** How many spans an edit changes: replaced or redacted. */
export function changedCount(state: EditorState): number {
  return state.reading.spans.size;
}

/** How many changed spans won't match, and the bar's words: once one is in its own font, all "will look different". */
export function warnedCount(state: EditorState): { count: number; label: string } {
  const places = changedMismatches(state);
  const approximate = places.some(({ span }) => span.why !== null);
  return { count: places.length, label: approximate ? "will look different" : "in a similar font" };
}

/** How many changed spans the server drew past their room, left long as typed: each warned on its mark, and counted in the bar. */
export function runLongCount({ reading, drawn }: Pick<EditorState, "reading" | "drawn">): number {
  return [...reading.spans.values()].filter(({ span, replaced }) => {
    const fit = drawn.fits.get(span.page)?.[span.id];
    return replaced && fit?.message_parts.some((part) => part.code === "too_long") === true;
  }).length;
}

/** The changed spans that won't match: in a similar font, set apart from the text around them, or missing letters the server found. */
export function changedMismatches({ reading, drawn }: Pick<EditorState, "reading" | "drawn">): MismatchPlace[] {
  return [...reading.spans.values()].flatMap((spanReading) => {
    const { span } = spanReading;
    const missing = drawn.fits.get(span.page)?.[span.id]?.missing ?? [];
    return placeOf(span, { changed: true, reading: spanReading, missing }) ?? [];
  });
}
