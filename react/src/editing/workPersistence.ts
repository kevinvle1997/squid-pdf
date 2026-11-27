import { sentenceOf } from "../bugs";
import {
  forgetPersisted,
  lacksSentences,
  type PersistedWork,
  persistFile,
  persistState,
} from "../documents/localPersistence";
import { announcing, type Editor, layoutOf, relaid, unsplitOf } from "./editor";
import { unexported } from "./export";
import { addOwnFace } from "./faces";
import type { History } from "./history";
import { newlyHeard, noticeLines, openingNoticesOf, warn } from "./notices";
import { UNEDITED } from "./project";

interface PersistedFont {
  readonly bytes: ArrayBuffer;
  readonly type: string;
}

export interface Work {
  readonly history: History;
  readonly fonts: readonly (readonly [string, PersistedFont])[];
  readonly quietFonts: readonly string[];
}

// Each font copy's bytes, read once however often the work is persisted.
const bytesOf = new WeakMap<Blob, Promise<ArrayBuffer>>();

async function persistedFonts(attached: ReadonlyMap<string, Blob>): Promise<Work["fonts"]> {
  const fonts = [...attached].map(async ([fontName, file]) => {
    const reading = bytesOf.get(file) ?? file.arrayBuffer();
    bytesOf.set(file, reading);
    return [fontName, { bytes: await reading, type: file.type }] as const;
  });
  return Promise.all(fonts);
}

export interface Persistence {
  file: boolean;
  history: History | null;
  until: number;
  forgetting: ReturnType<typeof setTimeout> | null;
}

export function isWork(persisted: unknown): persisted is Work {
  return (
    typeof persisted === "object" &&
    persisted !== null &&
    "history" in persisted &&
    "fonts" in persisted &&
    Array.isArray(persisted.fonts)
  );
}

export async function persistWork(editor: Editor): Promise<void> {
  const { history, doc, mutedFonts } = editor.store.get();
  const { persistence } = editor;
  const firstTime = !persistence.file;
  persistence.file = true;
  try {
    if (firstTime) await persistFile(editor.file);
    const fonts = await persistedFonts(editor.attached);
    await persistState({ doc, work: { history, fonts, quietFonts: [...mutedFonts] }, until: persistence.until });
    persistence.history = history;
    forgetAtUntil(persistence);
  } catch {
    // The browser stores nothing (a private window, or no room): leaving asks first instead.
    if (firstTime) persistence.file = false;
  }
}

export function persistUntil(editor: Editor, expiresAt: string): void {
  editor.persistence.until = Date.parse(expiresAt);
  if (editor.persistence.file) void persistWork(editor);
}

export function forgetAtUntil(persistence: Persistence): void {
  if (persistence.forgetting !== null) clearTimeout(persistence.forgetting);
  persistence.forgetting = setTimeout(
    () => {
      Object.assign(persistence, { file: false, history: null, forgetting: null });
      // Refused by the browser's storage: there was nothing persisted to forget.
      forgetPersisted().catch(() => undefined);
    },
    Math.max(0, persistence.until - Date.now()),
  );
}

export function stopForgetting(persistence: Persistence): void {
  if (persistence.forgetting !== null) clearTimeout(persistence.forgetting);
  persistence.forgetting = null;
}

export function wouldLoseWork(editor: Editor): boolean {
  const state = editor.store.get();
  return unexported(state) && (state.draft !== null || editor.persistence.history !== state.history);
}

export function restorePersisted(editor: Editor, { work, until }: PersistedWork<Work>): void {
  const { doc, layout } = editor.store.get();
  editor.store.set({
    history: work.history,
    ...relaid({ history: work.history, reading: UNEDITED }, doc, { base: unsplitOf(layout) }),
    mutedFonts: new Set(work.quietFonts),
  });
  for (const [fontName, { bytes, type }] of work.fonts) editor.attached.set(fontName, new Blob([bytes], { type }));
  Object.assign(editor.persistence, { file: true, history: work.history, until });
  forgetAtUntil(editor.persistence);
}

export function resumeWork(editor: Editor): void {
  const { reading } = editor.store.get();
  readStoredAgain(editor);
  if (reading.edits.length > 0) editor.queue.draw(reading);
  for (const [fontName, file] of editor.attached) void addOwnFace(fontName, file);
}

export function readStoredAgain(editor: Editor): void {
  if (lacksSentences(editor.store.get().doc)) void readAgain(editor);
}

async function readAgain(editor: Editor): Promise<void> {
  const { store, reopener } = editor;
  try {
    const doc = await reopener.readAgain();
    const { notices: before, drawn, reading } = store.get();
    const reopen = before.reopen?.tone === "warn" ? null : before.reopen;
    const notices = { ...before, document: openingNoticesOf(doc), reopen };
    const heard = newlyHeard(noticeLines(before, drawn, reading), noticeLines(notices, drawn, reading));
    const { draft } = store.get();
    const taken = draft?.kind === "span" ? (draft.split ?? null) : null;
    const laid = relaid(store.get(), doc, { base: layoutOf(doc), taken });
    store.set({ doc, ...laid, notices, ...(heard !== "" && { announcement: announcing(heard) }) });
  } catch (error) {
    const said = sentenceOf(error);
    store.set({ notices: { ...store.get().notices, reopen: warn(said) }, announcement: announcing(said) });
  }
}
