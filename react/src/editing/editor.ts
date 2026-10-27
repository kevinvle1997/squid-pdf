// One open document: its state, and the change every edit, undo and put-back goes through. Each action
// is a function of the editor in the module named for it (typing, export).
import type { Document, Edit, Sentences, SpanInfo } from "../api/types";
import { lacksSentences } from "../documents/localPersistence";
import { Reopener } from "../documents/reopen";
import type { Press } from "./caret";
import type { DateReading } from "./dates";
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
import {
  heardIn,
  NO_NOTICES,
  type Notices,
  newlyHeard,
  noticeLines,
  noticesAfterChange,
  openingLines,
  openingNoticesOf,
  plain,
} from "./notices";
import { leftOutSentence, putBackSentence } from "./pageDeletion";
import { disagreementsAfter, NO_DISAGREEMENTS, type PreviewDisagreement } from "./previewDisagreements";
import { project, type Reading, type SpanReading, textIn, UNEDITED } from "./project";
import { type Drawn, landedFitOf, NOTHING_DRAWN, RenderQueue } from "./render";
import { type FontsByObject, fontOf, fontsByObject, shownNameIn } from "./spanFonts";
import { createStore, type Store } from "./store";
import type { Draft } from "./typing";
import { fill, fillCounted } from "./words";
import { type Persistence, persistUntil, persistWork } from "./workPersistence";
import { FITTED, type Zoom } from "./zoom";

/**
 * A span whose mark takes focus as soon as it's drawn: back from its field, or put back from its
 * margin note. Its note stays shut until focus moves on, since it would cover the words. While it's
 * typed into, its field takes it instead: back from a value's editor in the slip.
 */
export interface FocusSpan {
  readonly spanId: string;
}

/**
 * A page whose own control takes focus as soon as it's drawn: its delete button, or its deleted page's
 * Put back, whichever is there. Brought into view at `block`.
 */
export interface FocusPage {
  readonly page: number;
  readonly block: ScrollLogicalPosition; // "nearest" where the control replaced the one pressed; "start" for a page gone to
}

/** Where focus is sent, to take as it's drawn. */
export type FocusTo = FocusSpan | FocusPage;

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
  readonly dates: DateReading | null; // what settles how its dates read: worked out as the first edit starts
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
  const atRest = { changed: false, reading: undefined, fit: undefined, font: undefined };
  const mismatches = doc.spans.flatMap((span) => placeOf(span, atRest) ?? []);
  return {
    spans: new Map(doc.spans.map((span) => [span.id, span])),
    pages,
    fonts,
    warnedFonts: new Set(mismatches.map(({ span }) => shownNameIn(fonts, span))),
    mismatchesAtRest: mismatchesOf(mismatches, { fonts, sentences: doc.sentences }),
    dates: null,
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
    notices: { ...NO_NOTICES, document: openingNoticesOf(opened) },
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
  const { notices, drawn: before, layout, doc, disagreements, reading } = store.get();
  const heard = newlyHeard(noticeLines(notices, before, reading), noticeLines(notices, drawn, reading));
  const after = disagreementsAfter(before, drawn, { fonts: layout.fonts, rules: doc.fit, disagreements });
  store.set({ drawn, disagreements: after, ...(heard !== "" && { announcement: announcing(heard) }) });
}

/** The editor is on screen: what opening the file found is heard, now its live region is there to announce it. */
export function announceOpening(editor: Editor): void {
  const { notices, reading } = editor.store.get();
  const found = openingLines(notices, reading);
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
    notices: noticesAfterChange(state.notices, { stale: lacksSentences(state.doc) }),
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
  const { history, layout, doc } = editor.store.get();
  const text = layout.spans.get(spanId)?.text ?? "";
  change(
    editor,
    { kind: "remove", ids: touching(history, spanId) },
    { announcement: announcing(fill(doc.sentences.put_back, { text })), focusTo: { spanId } },
  );
}

/** Where a key was pressed: on a page, anything on it or its tick in the page list, by the page. */
export interface PressedOn {
  readonly page: number | null;
}

/**
 * Take off the last thing done; announce what its span reads now and bring it into view. Focus stays, but on what
 * a page's delete or its undo takes away with the page: focus goes to the page's control in its place.
 */
export function undo(editor: Editor, on: PressedOn = { page: null }): void {
  const step = editor.store.get().history.done.at(-1);
  stepped(editor, { kind: "undo" }, { step, key: "back_to", on });
}

/** Bring back the last thing undone, announced and brought into view as undo does, and focus moved as it moves it. */
export function redo(editor: Editor, on: PressedOn = { page: null }): void {
  const step = editor.store.get().history.undone.at(-1);
  stepped(editor, { kind: "redo" }, { step, key: "changed_to", on });
}

function stepped(
  editor: Editor,
  action: HistoryAction,
  { step, key, on }: { step: Step | undefined; key: "back_to" | "changed_to"; on: PressedOn },
) {
  const pageDelete = step?.find(({ edit }) => edit.kind === "delete_page")?.edit;
  if (pageDelete?.kind === "delete_page") {
    pageStepped(editor, action, { page: pageDelete.page, on });
    return;
  }
  const spanId = step?.map((entry) => spanOf(entry.edit)).find((id) => id !== undefined);
  if (step === undefined || spanId === undefined) {
    change(editor, action);
    return;
  }
  const span = editor.store.get().layout.spans.get(spanId);
  change(editor, action, ({ reading, doc }) => ({
    announcement: announcing(fill(doc.sentences[key], { text: span === undefined ? "" : textIn(reading, span) })),
    scrollTo: scrollingTo(spanId),
  }));
}

/**
 * Undo or redo of a page's delete: said as the page now is, in or out. Focus on that page or its tick, which go
 * with it, goes to its control in its place (Put back, or its delete button); anywhere else it stays.
 */
function pageStepped(editor: Editor, action: HistoryAction, { page, on }: { page: number; on: PressedOn }): void {
  change(editor, action, ({ reading, doc }) => ({
    announcement: announcing(
      reading.deleted.has(page) ? leftOutSentence(doc) : putBackSentence(doc, { order: reading.order, page }),
    ),
    ...(on.page === page && { focusTo: { page, block: "nearest" } }),
  }));
}

/** Focus has left the span or page control it was sent to: the next visit is an ordinary one. */
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

/** The changed spans on the pages the download keeps: a page left out takes its edits with it. */
function keptChanges(reading: Reading): SpanReading[] {
  return [...reading.spans.values()].filter(({ span }) => !reading.deleted.has(span.page));
}

/** How many spans an edit changes on the pages the download keeps: until one does, the bar forecasts, a page's delete or not. */
export function changedWordsCount(state: EditorState): number {
  return keptChanges(state.reading).length;
}

/** How many changes the bar counts: each span an edit changes, replaced or redacted, and each page left out, but none on it. */
export function changedCount(state: EditorState): number {
  return changedWordsCount(state) + state.reading.deleted.size;
}

/** How many changed spans won't match: once one is in its own font, `different`, and all "will look different". */
export function warnedCount(state: EditorState): { count: number; different: boolean } {
  const places = changedMismatches(state);
  const different = places.some(({ verdict }) => verdict.fidelity === "approximate");
  return { count: places.length, different };
}

/**
 * The bar's count of `changed` changes, `count` of them warned, in the server's words: the part that warns kept apart,
 * as Find's count is. Nothing where a reading persisted by an earlier build lacks either half.
 */
export function changesShown(
  { changed, count, different }: { changed: number; count: number; different: boolean },
  sentences: Sentences,
): { plain: string; warned: string } {
  if (count === 0) return { plain: fillCounted(sentences.changes, changed), warned: "" };
  if (count === changed) {
    return {
      plain: "",
      warned: fillCounted(different ? sentences.changes_different : sentences.changes_similar, changed),
    };
  }
  const plain = fillCounted(sentences.changes_some, changed);
  const warned = fillCounted(different ? sentences.changes_some_different : sentences.changes_some_similar, count);
  if (plain === "" || warned === "") return { plain: "", warned: "" };
  return { plain, warned };
}

/** The bar's count before any change, in the server's words: the places an edit won't match, or nothing. */
export function atRestSentence({ count, reasons }: Mismatches, sentences: Sentences): string {
  if (count === 0) return "";
  // A reason the file's own font won't match makes some of them look different, as `different` does after a change.
  return fillCounted(reasons.length > 0 ? sentences.at_rest_different : sentences.at_rest_similar, count);
}

/** How many changed spans the server drew past their room, left long as typed: each warned on its mark, and counted in the bar. */
function runLongCount({ reading, drawn }: Pick<EditorState, "reading" | "drawn">): number {
  return keptChanges(reading).filter(({ span, replaced }) => {
    const fit = drawn.fits.get(span.page)?.[span.id];
    return replaced && fit?.message_parts.some((part) => part.code === "too_long") === true;
  }).length;
}

/**
 * The bar's count of the changes left long past their room, in the server's words: nothing when none is, or where a
 * reading persisted by an earlier build lacks it.
 */
export function runLongSentence(state: Pick<EditorState, "reading" | "drawn" | "doc">): string {
  const count = runLongCount(state);
  if (count === 0) return "";
  return fillCounted(state.doc.sentences.changes_too_long, count);
}

/** The changed spans whose edit won't match: in a similar font, or set apart from the text around them. */
export function changedMismatches({
  reading,
  drawn,
  layout,
}: Pick<EditorState, "reading" | "drawn" | "layout">): MismatchPlace[] {
  return keptChanges(reading).flatMap((spanReading) => {
    const { span } = spanReading;
    const fit = landedFitOf(drawn, spanReading);
    return placeOf(span, { changed: true, reading: spanReading, fit, font: fontOf(layout.fonts, span) }) ?? [];
  });
}
