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
  type Entry,
  entriesOf,
  type History,
  type HistoryAction,
  historyReducer,
  newTextIdsOf,
  type Step,
  spansOf,
  touching,
} from "./history";
import type { FailedImage } from "./images";
import { type Line, linesBySpan } from "./lines";
import { type Mismatches, type MismatchPlace, mismatchesOf, placeOf } from "./mismatches";
import { focusAfterNewText, keptNewTexts, newTextPlaceOf } from "./newText";
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
import { project, type Reading, type Redacted, redactedBetween, type SpanReading, textIn, UNEDITED } from "./project";
import { type Drawn, landedFitOf, landedNewTextFitOf, NOTHING_DRAWN, RenderQueue } from "./render";
import { NO_SHIPPED_FACES, type ShippedFaces } from "./shippedFaces";
import { type FontsByObject, fontOf, fontsByObject, shownNameIn } from "./spanFonts";
import { cutsOf, liveEntries, type Part, type Region, spanIdOf, splitSpans, wireEdit } from "./splits";
import { createStore, type Store } from "./store";
import type { Draft } from "./typing";
import { fill, fillCounted } from "./words";
import { type Persistence, persistUntil, persistWork, stopForgetting } from "./workPersistence";
import { FITTED, type Zoom } from "./zoom";

/** Its note stays shut until focus moves on, since it would cover the words. */
export interface FocusSpan {
  readonly spanId: string;
  readonly inPlace?: boolean;
}

export interface FocusPage {
  readonly page: number;
  readonly block: ScrollLogicalPosition;
  readonly inPlace?: boolean;
}

export interface FocusTick {
  readonly tick: number;
}

export interface FocusNewText {
  readonly newTextId: number;
  readonly inPlace?: boolean;
}

export interface FocusField {
  readonly field: true;
}

export interface FocusFaces {
  readonly faces: true;
}

export interface FocusValueEditor {
  readonly valueEditor: string;
}

export type FocusTo = FocusSpan | FocusPage | FocusTick | FocusNewText | FocusField | FocusFaces | FocusValueEditor;

export interface Unredacting {
  readonly spanId: string;
  readonly press: Press | null;
  readonly typed: Typed | null;
}

export interface Typed {
  readonly text: string;
  readonly runs: readonly string[] | null;
  readonly caret: number | null;
}

export interface Announcement {
  readonly text: string;
  readonly count: number;
}

export interface ScrollTo {
  readonly spanId: string;
  readonly count: number;
  readonly block: ScrollLogicalPosition;
}

// Module-wide: two editors' counts never meet, so one will do.
let lastCount = 0;

export function announcing(text: string): Announcement {
  lastCount += 1;
  return { text, count: lastCount };
}

export function scrollingTo(spanId: string, block: ScrollLogicalPosition = "nearest"): ScrollTo {
  lastCount += 1;
  return { spanId, count: lastCount, block };
}

export interface Layout {
  readonly spans: ReadonlyMap<string, SpanInfo>; // by id: where a split cuts a span, its parts, each by its own
  readonly ordered: readonly SpanInfo[]; // them all, in the document's order
  readonly parts: ReadonlyMap<string, Part>; // each part a split cuts short of its span, by its id
  readonly unsplit?: Layout; // the document's own, where a split makes this one another
  readonly splitKey?: string; // where it's split: the same while the splits are
  readonly pages: ReadonlyMap<number, readonly SpanInfo[]>;
  readonly fonts: FontsByObject;
  readonly lines: ReadonlyMap<string, Line>;
  readonly warnedFonts: ReadonlySet<string>;
  readonly mismatchesAtRest: Mismatches;
}

export interface EditorState {
  readonly doc: Document;
  readonly layout: Layout;
  readonly dates: DateReading | null;
  readonly scale: number;
  readonly zoom: Zoom;
  readonly pageInView: number;
  readonly history: History;
  readonly reading: Reading;
  readonly drawn: Drawn;
  readonly disagreements: ReadonlyMap<number, PreviewDisagreement>;
  readonly draft: Draft | null;
  readonly unredacting: Unredacting | null;
  readonly focusTo: FocusTo | null;
  readonly notices: Notices;
  readonly announcement: Announcement;
  readonly scrollTo: ScrollTo | null;
  readonly exporting: boolean;
  readonly exported: readonly Edit[];
  readonly attaching: string | null;
  readonly focusFont: string | null;
  readonly shownMismatches: ReadonlySet<string>;
  readonly mutedFonts: ReadonlySet<string>;
  readonly images: ReadonlyMap<number, FailedImage>;
  readonly find: FindState;
  readonly shortcutsOpen: boolean;
  readonly shippedFaces: ShippedFaces;
  readonly nudged: number | null;
}

export interface Editor {
  readonly store: Store<EditorState>;
  readonly file: File;
  readonly attached: Map<string, Blob>;
  readonly reopener: Reopener;
  readonly queue: RenderQueue;
  readonly near: Set<number>;
  readonly retries: Map<number, ReturnType<typeof setTimeout>>;
  readonly finding: { timer?: ReturnType<typeof setTimeout> };
  readonly ahead: ExportAhead;
  readonly persistence: Persistence;
}

export function layoutOf(doc: Document): Layout {
  const pages = spansByPage(doc.spans);
  const fonts = fontsByObject(doc.fonts);
  const atRest = { changed: false, reading: undefined, fit: undefined, font: undefined };
  const mismatches = doc.spans.flatMap((span) => placeOf(span, atRest) ?? []);
  return {
    spans: new Map(doc.spans.map((span) => [span.id, span])),
    ordered: doc.spans,
    parts: new Map(),
    pages,
    lines: linesBySpan(doc),
    fonts,
    warnedFonts: new Set(mismatches.map(({ span }) => shownNameIn(fonts, span))),
    mismatchesAtRest: mismatchesOf(mismatches, { fonts, sentences: doc.sentences }),
  };
}

/**
 * What `entries` read as on `doc`, and the layout their splits make of its own, `base`'s: the open field's split
 * `taken` too. While the splits are the same, the layout is `previous`'s, so nothing that reads it draws again.
 */
export function readingOf(
  doc: Document,
  { base, entries, taken = null }: { base: Layout; entries: readonly Entry[]; taken?: Region | null },
  previous: { layout?: Layout; reading: Reading } = { reading: UNEDITED },
): { layout: Layout; reading: Reading } {
  const live = liveEntries(base, entries, taken);
  const layout = splitLayout(base, cutsOf(base, live, taken), previous.layout);
  const reading = project({ spans: [...layout.ordered], pages: doc.pages }, live, previous.reading);
  return { layout, reading };
}

/** The document's own layout, however it's split now. */
export function unsplitOf(layout: Layout): Layout {
  return layout.unsplit ?? layout;
}

/** `base` split at `cuts`: itself with none, and `previous` while it's split there already. */
function splitLayout(base: Layout, cuts: ReadonlyMap<string, readonly number[]>, previous?: Layout): Layout {
  if (cuts.size === 0) return base;
  const splitKey = JSON.stringify([...cuts]);
  if (previous?.unsplit === base && previous.splitKey === splitKey) return previous;
  const split = splitSpans(base, cuts);
  return { ...base, ...split, pages: spansByPage(split.ordered), unsplit: base, splitKey };
}

/** Each page's spans, in order. */
function spansByPage(spans: readonly SpanInfo[]): Map<number, SpanInfo[]> {
  const pages = new Map<number, SpanInfo[]>();
  for (const span of spans) {
    const onPage = pages.get(span.page);
    if (onPage === undefined) pages.set(span.page, [span]);
    else onPage.push(span);
  }
  return pages;
}

export function createEditor(file: File, opened: Document, scale: number): Editor {
  const store = createStore<EditorState>({
    doc: opened,
    layout: layoutOf(opened),
    dates: null,
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
    shippedFaces: NO_SHIPPED_FACES,
    nudged: null,
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
    persistence: { file: false, history: null, until: Date.parse(opened.expires_at), forgetting: null },
  };
  return editor;
}

async function reopened(editor: Editor, doc: Document): Promise<Document> {
  const { store, queue } = editor;
  const { judged, refusal } = await reattach(editor, doc);
  const reopen = plain(doc.sentences.reopened);
  const notices = { ...store.get().notices, reopen, ...(refusal !== null && { font: refusal }) };
  const heard = refusal === null ? reopen.text : `${reopen.text} ${refusal.text}`;
  const { draft } = store.get();
  const taken = draft?.kind === "span" ? (draft.split ?? null) : null;
  const laid = relaid(store.get(), judged, { base: layoutOf(judged), taken });
  store.set({ doc: judged, ...laid, notices, announcement: announcing(heard) });
  // Pages drawn with a copy now refused no longer show what export would give.
  if (refusal !== null) queue.redraw(store.get().reading);
  return judged;
}

function drawnNow(store: Store<EditorState>, drawn: Drawn): void {
  const { notices, drawn: before, layout, doc, disagreements, reading } = store.get();
  const heard = newlyHeard(noticeLines(notices, before, reading), noticeLines(notices, drawn, reading));
  const after = disagreementsAfter(before, drawn, { layout, rules: doc.fit, disagreements });
  store.set({ drawn, disagreements: after, ...(heard !== "" && { announcement: announcing(heard) }) });
}

export function announceOpening(editor: Editor): void {
  const { notices, reading } = editor.store.get();
  const found = openingLines(notices, reading);
  if (found.length > 0) announce(editor, heardIn(found));
}

export type Also = Partial<EditorState> | ((next: EditorState) => Partial<EditorState>);

export function changed(state: EditorState, actions: readonly HistoryAction[]): EditorState {
  const history = actions.reduce(historyReducer, state.history);
  if (history === state.history) return state;
  const base = unsplitOf(state.layout);
  const { layout, reading } = readingOf(state.doc, { base, entries: entriesOf(history) }, state);
  return {
    ...state,
    history,
    layout,
    reading,
    notices: noticesAfterChange(state.notices, { stale: lacksSentences(state.doc) }),
    find: findAfterChange({ ...state, layout }, reading),
  };
}

export function change(editor: Editor, action: HistoryAction | readonly HistoryAction[], also: Also = {}): void {
  const { store, queue } = editor;
  const state = store.get();
  const next = changed(state, [action].flat());
  const rest = typeof also === "function" ? also(next) : also;
  // A split the field held goes as it closes, with nothing typed against it to keep it.
  const dropsSplit = state.draft?.kind === "span" && state.draft.split !== undefined && rest.draft === null;
  if (next === state) {
    const unsplit: Partial<EditorState> = dropsSplit ? relaid(state, state.doc, { base: unsplitOf(state.layout) }) : {};
    if (Object.keys(rest).length > 0) store.set({ ...unsplit, ...rest, ...focusedOn(unsplit.layout, rest) });
    return;
  }
  const { history, layout, reading, notices, find } = next;
  store.set({ history, layout, reading, notices, find, ...rest, ...focusedOn(layout, rest) });
  queue.draw(reading);
  void persistWork(editor);
}

/** `edit`, made of the page as `layout` splits it, as the history keeps it: a part's, as one of its span's. */
export function asWired<E extends Edit>(layout: Layout, edit: E): Edit {
  return layout.parts.size === 0 ? edit : wireEdit(unsplitOf(layout), layout.parts, edit);
}

/** Focus sent to a span a split no longer shows as it was: its line's mark, or the span's. */
function focusedOn(layout: Layout | undefined, rest: Partial<EditorState>): Partial<EditorState> {
  const target = rest.focusTo;
  if (layout === undefined || target === null || target === undefined || !("spanId" in target)) return {};
  const id = layout.spans.has(target.spanId) ? target.spanId : spanIdOf(target.spanId);
  const mark = layout.lines.get(id)?.id ?? id;
  return mark === target.spanId ? {} : { focusTo: { ...target, spanId: mark } };
}

/** `doc`'s layout from `base`, split as the history and the open field have it, and what it reads as. */
export function relaid(
  state: Pick<EditorState, "history" | "reading">,
  doc: Document,
  { base, taken = null }: { base: Layout; taken?: Region | null },
): { layout: Layout; reading: Reading } {
  return readingOf(doc, { base, entries: entriesOf(state.history), taken }, { reading: state.reading });
}

/** Put a span back as the document had it, with the rest of its line, from its margin note; focus goes to its mark. */
export function putBack(editor: Editor, spanId: string): void {
  const { history, layout, doc } = editor.store.get();
  const span = layout.spans.get(spanId);
  const line = layout.lines.get(spanId);
  const spans = line?.spans ?? (span === undefined ? [] : [span]);
  const ids = new Set(spans.flatMap(({ id }) => [...touching(history, id)]));
  const text = spans.map((each) => each.text).join("");
  change(
    editor,
    { kind: "remove", ids },
    { announcement: announcing(fill(doc.sentences.put_back, { text })), focusTo: { spanId: line?.id ?? spanId } },
  );
}

export interface PressedOn {
  readonly page: number | null;
  readonly newTextId: number | null;
  readonly marginNote: boolean;
}

const PRESSED_NOWHERE: PressedOn = { page: null, newTextId: null, marginNote: false };

export function undo(editor: Editor, on: PressedOn = PRESSED_NOWHERE): void {
  const step = editor.store.get().history.done.at(-1);
  stepped(editor, { kind: "undo" }, { step, key: "back_to", on });
}

export function redo(editor: Editor, on: PressedOn = PRESSED_NOWHERE): void {
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
  const newText = step?.find(({ edit }) => edit.kind === "insert");
  if (step !== undefined && newText !== undefined) {
    newTextStepped(editor, action, { step, entry: newText, key, on });
    return;
  }
  const spanId = step?.flatMap((entry) => spansOf(entry.edit))[0];
  if (step === undefined || spanId === undefined) {
    change(editor, action);
    return;
  }
  const { layout, reading: before } = editor.store.get();
  const span = layout.spans.get(spanId);
  const redacting = step.some(({ edit }) => edit.kind === "redact");
  const redaction = redacting ? { was: before.spans.get(spanId)?.redacted ?? null } : null;
  const line = layout.lines.get(spanId);
  change(editor, action, ({ reading, doc }) => ({
    announcement: announcing(stepAnnouncement(doc.sentences, { key, span, line, reading, redaction })),
    scrollTo: scrollingTo(spanId),
    ...(on.marginNote && { focusTo: { spanId } }),
  }));
}

function stepAnnouncement(
  sentences: Sentences,
  {
    key,
    span,
    line,
    reading,
    redaction,
  }: {
    key: "back_to" | "changed_to";
    span: SpanInfo | undefined;
    line: Line | undefined;
    reading: Reading;
    redaction: { was: Redacted | null } | null;
  },
): string {
  if (span === undefined) return fill(sentences[key], { text: "" });
  const spans = line?.spans ?? [span];
  if (redaction === null) return fill(sentences[key], { text: spans.map((each) => textIn(reading, each)).join("") });
  const { was } = redaction;
  const now = reading.spans.get(span.id)?.redacted ?? null;
  if (key === "back_to") return fill(sentences.put_back, { text: redactedBetween(span.text, { from: was, to: now }) });
  return fill(sentences.redacted, { text: redactedBetween(span.text, { from: now, to: was }) });
}

function newTextStepped(
  editor: Editor,
  action: HistoryAction,
  { step, entry, key, on }: { step: Step; entry: Entry; key: "back_to" | "changed_to"; on: PressedOn },
): void {
  const { history, reading: before } = editor.store.get();
  const newTextId = newTextIdsOf([...entriesOf(history), ...step]).get(entry.id);
  const had = keptNewTexts(before).find((text) => text.newTextId === newTextId)?.edit.text ?? "";
  change(editor, action, ({ reading, layout, doc }) => {
    const now = keptNewTexts(reading).find((text) => text.newTextId === newTextId);
    if (now !== undefined) {
      const markShown = on.marginNote && editor.near.has(now.edit.page);
      return {
        announcement: announcing(fill(doc.sentences[key], { text: now.edit.text })),
        ...(markShown && { focusTo: { newTextId: now.newTextId } }),
      };
    }
    const gone = entry.edit.kind === "insert" ? entry.edit : undefined;
    const focusMoves = on.newTextId === newTextId || on.marginNote;
    return {
      announcement: announcing(fill(doc.sentences.new_text_taken_out, { text: had })),
      ...(gone !== undefined && focusMoves && sendingFocus(focusAfterNewText({ layout, reading }, gone))),
    };
  });
}

function sendingFocus(target: FocusTo): Partial<Pick<EditorState, "focusTo" | "scrollTo">> {
  return "spanId" in target ? { focusTo: target, scrollTo: scrollingTo(target.spanId) } : { focusTo: target };
}

function pageStepped(editor: Editor, action: HistoryAction, { page, on }: { page: number; on: PressedOn }): void {
  change(editor, action, ({ reading, doc }) => ({
    announcement: announcing(
      reading.deleted.has(page) ? leftOutSentence(doc) : putBackSentence(doc, { order: reading.order, page }),
    ),
    ...(on.page === page && { focusTo: { page, block: "nearest" } }),
  }));
}

export function focusMoved(editor: Editor): void {
  editor.store.set({ focusTo: null });
}

export function closeEditor(editor: Editor): void {
  editor.queue.stop();
  for (const retry of editor.retries.values()) clearTimeout(retry);
  editor.retries.clear();
  clearTimeout(editor.finding.timer);
  clearTimeout(editor.ahead.timer);
  editor.ahead.started?.ask.abort();
  stopForgetting(editor.persistence);
}

export function announce(editor: Editor, text: string): void {
  editor.store.set({ announcement: announcing(text) });
}

function keptReadings(reading: Reading): SpanReading[] {
  return [...reading.spans.values()].filter(({ span }) => !reading.deleted.has(span.page));
}

function keptChanges(reading: Reading): SpanReading[] {
  return keptReadings(reading).filter(({ replaced, redacted }) => replaced || redacted !== null);
}

export function changedWordsCount(state: EditorState): number {
  const changedSpans = keptChanges(state.reading).map(({ span }) => span.id);
  return lineCount(state.layout, changedSpans) + keptNewTexts(state.reading).length;
}

function lineCount(layout: Pick<Layout, "lines">, spanIds: readonly string[]): number {
  return new Set(spanIds.map((id) => layout.lines.get(id)?.id ?? id)).size;
}

export function changedCount(state: EditorState): number {
  return changedWordsCount(state) + state.reading.deleted.size;
}

export function warnedCount(state: EditorState): { count: number; different: boolean } {
  const places = changedMismatches(state);
  const different = places.some(({ verdict }) => verdict.fidelity === "approximate");
  const spanIds = places.map(({ span }) => span.id).filter((id) => state.reading.spans.has(id));
  const newTextCount = places.length - spanIds.length;
  return { count: lineCount(state.layout, spanIds) + newTextCount, different };
}

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

export function atRestSentence({ count, reasons }: Mismatches, sentences: Sentences): string {
  if (count === 0) return "";
  return fillCounted(reasons.length > 0 ? sentences.at_rest_different : sentences.at_rest_similar, count);
}

function runLongCount({ reading, drawn, layout }: Pick<EditorState, "reading" | "drawn" | "layout">): number {
  const long = keptChanges(reading).filter(({ span, replaced }) => {
    const fit = drawn.fits.get(span.page)?.[span.id];
    return replaced && fit?.message_parts.some((part) => part.code === "too_long") === true;
  });
  return lineCount(
    layout,
    long.map(({ span }) => span.id),
  );
}

export function runLongSentence(state: Pick<EditorState, "reading" | "drawn" | "doc" | "layout">): string {
  const count = runLongCount(state);
  if (count === 0) return "";
  return fillCounted(state.doc.sentences.changes_too_long, count);
}

export function changedMismatches({
  reading,
  drawn,
  layout,
}: Pick<EditorState, "reading" | "drawn" | "layout">): MismatchPlace[] {
  const spans = keptReadings(reading).flatMap((spanReading) => {
    const { span } = spanReading;
    const fit = landedFitOf(drawn, spanReading);
    return placeOf(span, { changed: true, reading: spanReading, fit, font: fontOf(layout.fonts, span) }) ?? [];
  });
  const texts = keptNewTexts(reading).flatMap(
    (text) => newTextPlaceOf(layout, { text, fit: landedNewTextFitOf(drawn, text) }) ?? [],
  );
  return [...spans, ...texts];
}
