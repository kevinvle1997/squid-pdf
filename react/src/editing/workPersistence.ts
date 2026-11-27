// The work on the open document, persisted in this browser as it changes (documents/localPersistence.ts),
// so a reload or a closed tab loses none of it; and the editor restored from work persisted before.

import type { Document } from "../api/types";
import { sentenceOf } from "../bugs";
import {
  forgetPersisted,
  lacksSentences,
  type PersistedWork,
  persistFile,
  persistState,
  persistUntil,
} from "../documents/localPersistence";
import { announcing, type Editor, layoutOf } from "./editor";
import { unexported } from "./export";
import { addOwnFace } from "./faces";
import { entriesOf, type History } from "./history";
import { newlyHeard, noticeLines, openingNoticesOf, warn } from "./notices";
import { project, UNEDITED } from "./project";

/** A font copy as it's stored: its bytes, and their type, which goes with them to the server. */
interface PersistedFont {
  readonly bytes: ArrayBuffer;
  readonly type: string;
}

/** What's persisted of the work on a document: its history, the user's copies of its fonts, and the fonts muted. */
export interface Work {
  readonly history: History;
  readonly fonts: readonly (readonly [string, PersistedFont])[];
  readonly quietFonts: readonly string[]; // the muted fonts, under the name saved work is stored with
}

// Each font copy's bytes, read once however often the work is persisted.
const bytesOf = new WeakMap<Blob, Promise<ArrayBuffer>>();

/** The user's copies of the document's fonts, as they're stored. */
async function persistedFonts(attached: ReadonlyMap<string, Blob>): Promise<Work["fonts"]> {
  const fonts = [...attached].map(async ([fontName, file]) => {
    const reading = bytesOf.get(file) ?? file.arrayBuffer();
    bytesOf.set(file, reading);
    return [fontName, { bytes: await reading, type: file.type }] as const;
  });
  return Promise.all(fonts);
}

/** What's been persisted of the open document: whether its file is, the reading and the history last written, and until when. */
export interface Persistence {
  file: boolean;
  doc: Document | null; // the server's reading written, or being written: written again only once it changes
  history: History | null;
  until: number; // when the server forgets the document, in ms since the epoch: persisted here as long
  forgetting: ReturnType<typeof setTimeout> | null; // forgets what's persisted here at `until`, while the page is open
  writing: Promise<void>; // the writes asked for, one after another: the last asked is the last written
}

/** Whether what was persisted is work this editor can start from. */
export function isWork(persisted: unknown): persisted is Work {
  return (
    typeof persisted === "object" &&
    persisted !== null &&
    "history" in persisted &&
    "fonts" in persisted &&
    Array.isArray(persisted.fonts)
  );
}

/**
 * Persist the work, after the writes asked before: once written, leaving loses none of it. One at a time, so a write
 * that started earlier never lands over one that started later.
 */
export function persistWork(editor: Editor): Promise<void> {
  const { persistence } = editor;
  persistence.writing = persistence.writing.then(() => persistNow(editor));
  return persistence.writing;
}

/** Persist the work as it is now, the file and the server's reading too the first time and the reading again once it changes. */
async function persistNow(editor: Editor): Promise<void> {
  const { history, doc, mutedFonts, reading } = editor.store.get();
  const { persistence } = editor;
  const firstTime = !persistence.file;
  // Persisting the file afresh forgets the reading stored with the one before.
  const docChanged = firstTime || persistence.doc !== doc;
  // Taken at once, as the file is, so a reply meanwhile writes no second copy.
  Object.assign(persistence, { file: true, doc });
  try {
    if (firstTime) await persistFile(editor.file);
    const fonts = await persistedFonts(editor.attached);
    const work = { history, fonts, quietFonts: [...mutedFonts] };
    await persistState({ doc, work, until: persistence.until, order: reading.order }, { docChanged });
    persistence.history = history;
    forgetAtUntil(persistence);
  } catch {
    // The browser stores nothing (a private window, or no room): leaving asks first instead.
    if (firstTime) persistence.file = false;
    if (docChanged) persistence.doc = null;
  }
}

/** The server's hour moved, as each of its replies says: what's persisted here lasts as long, with the reading it's of. */
export function untilMoved(editor: Editor, expiresAt: string): void {
  const { persistence } = editor;
  persistence.until = Date.parse(expiresAt);
  if (!persistence.file) return;
  // The document opened again on the server, or read again, since the work was persisted: the hour goes with it.
  if (persistence.doc !== editor.store.get().doc) {
    void persistWork(editor);
    return;
  }
  // Refused (a private window, say): the next change persists the hour with the work.
  persistUntil(persistence.until).catch(() => undefined);
  forgetAtUntil(persistence);
}

/**
 * Forget what's persisted here as the server's hour runs out while the page is open, so the copy lasts as long as
 * the landing says. The next change persists afresh, file and all; until then, leaving asks first.
 */
export function forgetAtUntil(persistence: Persistence): void {
  if (persistence.forgetting !== null) clearTimeout(persistence.forgetting);
  persistence.forgetting = setTimeout(
    () => {
      Object.assign(persistence, { file: false, doc: null, history: null, forgetting: null });
      // Refused by the browser's storage: there was nothing persisted to forget.
      forgetPersisted().catch(() => undefined);
    },
    Math.max(0, persistence.until - Date.now()),
  );
}

/** The editor is closing: what's persisted here stays for the landing to offer, so nothing forgets it now. */
export function stopForgetting(persistence: Persistence): void {
  if (persistence.forgetting !== null) clearTimeout(persistence.forgetting);
  persistence.forgetting = null;
}

/** Whether leaving now would lose work: changes no export took, not yet persisted here, or words still being typed. */
export function wouldLoseWork(editor: Editor): boolean {
  const state = editor.store.get();
  return unexported(state) && (state.draft !== null || editor.persistence.history !== state.history);
}

/** A new editor restores work persisted here before, before it's drawn: persisted already, file and all. */
export function restorePersisted(editor: Editor, { work, until }: PersistedWork<Work>): void {
  const { doc } = editor.store.get();
  editor.store.set({
    history: work.history,
    reading: project(doc, entriesOf(work.history), UNEDITED),
    mutedFonts: new Set(work.quietFonts),
  });
  for (const [fontName, { bytes, type }] of work.fonts) editor.attached.set(fontName, new Blob([bytes], { type }));
  // The reading is written again with the first change: it may have been read from the shape before.
  Object.assign(editor.persistence, { file: true, doc: null, history: work.history, until });
  forgetAtUntil(editor.persistence);
}

/** The editor restored from persisted work: what it reads is drawn, the user's copies of its fonts preview, and a stale reading is read again. */
export function resumeWork(editor: Editor): void {
  const { reading } = editor.store.get();
  readStoredAgain(editor);
  if (reading.edits.length > 0) editor.queue.draw(reading);
  for (const [fontName, file] of editor.attached) void addOwnFace(fontName, file);
}

/**
 * A reading of the document persisted by an earlier build lacks a sentence this one reads: the server's is asked
 * for, as the editor opens and again as the connection comes back, until it lands.
 */
export function readStoredAgain(editor: Editor): void {
  if (lacksSentences(editor.store.get().doc)) void readAgain(editor);
}

/**
 * The server's reading of the document asked for again, in place of the one persisted, and persisted, with what opening
 * it finds: a line that's new under the bar is heard. Why not, in the bar if it fails.
 */
async function readAgain(editor: Editor): Promise<void> {
  const { store, reopener } = editor;
  try {
    const doc = await reopener.readAgain();
    const { notices: before, drawn, reading } = store.get();
    // A failure said before, this one's or opening again's, is over: the document is there.
    const reopen = before.reopen?.tone === "warn" ? null : before.reopen;
    const notices = { ...before, document: openingNoticesOf(doc), reopen };
    const heard = newlyHeard(noticeLines(before, drawn, reading), noticeLines(notices, drawn, reading));
    store.set({ doc, layout: layoutOf(doc), notices, ...(heard !== "" && { announcement: announcing(heard) }) });
    // Persisted in place of the one before, so a reload doesn't ask for it again.
    if (editor.persistence.file) void persistWork(editor);
  } catch (error) {
    const said = sentenceOf(error);
    store.set({ notices: { ...store.get().notices, reopen: warn(said) }, announcement: announcing(said) });
  }
}
