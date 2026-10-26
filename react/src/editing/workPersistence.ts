// The work on the open document, persisted in this browser as it changes (documents/localPersistence.ts),
// so a reload or a closed tab loses none of it; and the editor restored from work persisted before.
import { type PersistedWork, persistFile, persistState } from "../documents/localPersistence";
import type { Editor } from "./editor";
import { unexported } from "./export";
import { addOwnFace } from "./faces";
import { entriesOf, type History } from "./history";
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

/** What's been persisted of the open document: whether its file is, the history last written, and until when. */
export interface Persistence {
  file: boolean;
  history: History | null;
  until: number; // when the server forgets the document, in ms since the epoch: persisted here as long
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

/** Persist the work as it is now, the file too the first time: once written, leaving loses none of it. */
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
  } catch {
    // The browser stores nothing (a private window, or no room): leaving asks first instead.
    if (firstTime) persistence.file = false;
  }
}

/** The server's hour moved, as each of its replies says: what's persisted here lasts as long. */
export function persistUntil(editor: Editor, expiresAt: string): void {
  editor.persistence.until = Date.parse(expiresAt);
  if (editor.persistence.file) void persistWork(editor);
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
    reading: project(doc.spans, entriesOf(work.history), UNEDITED),
    mutedFonts: new Set(work.quietFonts),
  });
  for (const [fontName, { bytes, type }] of work.fonts) editor.attached.set(fontName, new Blob([bytes], { type }));
  Object.assign(editor.persistence, { file: true, history: work.history, until });
}

/** The editor restored from persisted work: what it reads is drawn, and the user's copies of its fonts preview. */
export function resumeWork(editor: Editor): void {
  const { reading } = editor.store.get();
  if (reading.edits.length > 0) editor.queue.draw(reading);
  for (const [fontName, file] of editor.attached) void addOwnFace(fontName, file);
}
