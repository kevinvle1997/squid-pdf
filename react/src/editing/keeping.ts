// The work on the open document, kept in this browser as it changes (documents/kept.ts), so a
// reload or a closed tab loses none of it; and the editor started again from work kept before.
import { keepFile, keepState } from "../documents/kept";
import { type Editor, unexported } from "./editor";
import { addOwnFace } from "./faces";
import { entriesOf, type History } from "./history";
import { project, UNEDITED } from "./project";

/** A font copy as it's kept: its bytes, and their type, which goes with them to the server. */
interface KeptFont {
  readonly bytes: ArrayBuffer;
  readonly type: string;
}

/** What's kept of the work on a document: its history, and the user's copies of its fonts, by font. */
export interface Work {
  readonly history: History;
  readonly fonts: readonly (readonly [string, KeptFont])[];
}

// Each font copy's bytes, read once however often the work is kept.
const bytesOf = new WeakMap<Blob, Promise<ArrayBuffer>>();

/** The user's copies of the document's fonts, as they're kept. */
async function keptFonts(attached: ReadonlyMap<string, Blob>): Promise<Work["fonts"]> {
  const kept = [...attached].map(async ([fontName, file]) => {
    const reading = bytesOf.get(file) ?? file.arrayBuffer();
    bytesOf.set(file, reading);
    return [fontName, { bytes: await reading, type: file.type }] as const;
  });
  return Promise.all(kept);
}

/** What's been kept of the open document: whether its file is, the history last written, and until when. */
export interface Keeping {
  file: boolean;
  history: History | null;
  until: number; // when the server forgets the document, in ms since the epoch: kept here as long
}

/** Whether what was kept is work this editor can start from. */
export function isWork(kept: unknown): kept is Work {
  return typeof kept === "object" && kept !== null && "history" in kept && "fonts" in kept && Array.isArray(kept.fonts);
}

/** Keep the work as it is now, the file too the first time: once written, leaving loses none of it. */
export async function keepWork(editor: Editor): Promise<void> {
  const { history, doc } = editor.store.get();
  const { keeping } = editor;
  const firstTime = !keeping.file;
  keeping.file = true;
  try {
    if (firstTime) await keepFile(editor.file);
    const fonts = await keptFonts(editor.attached);
    await keepState({ doc, work: { history, fonts }, until: keeping.until });
    keeping.history = history;
  } catch {
    // The browser keeps nothing (a private window, or no room): leaving asks first instead.
    if (firstTime) keeping.file = false;
  }
}

/** The server's hour moved, as each of its replies says: what's kept here is kept as long. */
export function keepUntil(editor: Editor, expiresAt: string): void {
  editor.keeping.until = Date.parse(expiresAt);
  if (editor.keeping.file) void keepWork(editor);
}

/** Whether leaving now would lose work: changes no export took, not yet kept here, or words still being typed. */
export function atRisk(editor: Editor): boolean {
  const state = editor.store.get();
  return unexported(state) && (state.draft !== null || editor.keeping.history !== state.history);
}

/** A new editor takes up work kept here before, before it's drawn: kept already, file and all. */
export function takeUp(editor: Editor, { work, until }: { work: Work; until: number }): void {
  const { doc } = editor.store.get();
  editor.store.set({ history: work.history, reading: project(doc.spans, entriesOf(work.history), UNEDITED) });
  for (const [fontName, { bytes, type }] of work.fonts) editor.attached.set(fontName, new Blob([bytes], { type }));
  Object.assign(editor.keeping, { file: true, history: work.history, until });
}

/** The editor started from kept work: what it reads is drawn, and the user's copies of its fonts preview. */
export function resumeWork(editor: Editor): void {
  const { reading } = editor.store.get();
  if (reading.edits.length > 0) editor.queue.draw(reading);
  for (const [fontName, file] of editor.attached) void addOwnFace(fontName, file);
}
