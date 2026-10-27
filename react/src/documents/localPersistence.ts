// A document persisted in this browser until the server's own hour runs out, so a reload or a closed tab
// loses no work. Files are stored as bytes: Safari's private windows, as WebKit's tests run, won't store a Blob.
import type { Document, Sentences, SpanInfo } from "../api/types";
import { sentenceOf } from "../bugs";

/** What's persisted of a document: the file, the server's reading of it, the work done to it, and until when. */
export interface Persisted<Work> {
  readonly file: File;
  readonly doc: Document;
  readonly work: Work;
  readonly until: number; // when the server forgets it too, in ms since the epoch
}

/** The work persisted with a document, and until when: what an editor opened again restores. */
export type PersistedWork<Work> = Pick<Persisted<Work>, "work" | "until">;

// The names saved work is stored under: renaming one loses every user's saved work.
const DATABASE = "squid-pdf";
const STORE = "kept";
const FILE = "file";
const STATE = "state";
// What's stored changes shape with the code: one stored in another shape is forgotten.
const FORMAT = 1;

let connection: Promise<IDBDatabase> | undefined;

/** The browser's database the document is persisted in, opened on first use. */
function database(): Promise<IDBDatabase> {
  connection ??= new Promise((resolve, reject) => {
    const request = indexedDB.open(DATABASE, 1);
    request.onupgradeneeded = () => request.result.createObjectStore(STORE);
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  return connection;
}

/** Run `use` on the store in one transaction, once it's written. */
async function inStore<T>(mode: IDBTransactionMode, use: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const transaction = (await database()).transaction(STORE, mode);
  const request = use(transaction.objectStore(STORE));
  return new Promise((resolve, reject) => {
    transaction.oncomplete = () => resolve(request.result);
    transaction.onerror = () => reject(transaction.error);
    transaction.onabort = () => reject(transaction.error);
  });
}

/** A file as it's stored: its bytes, and what makes them a file again. */
interface PersistedFile {
  readonly bytes: ArrayBuffer;
  readonly name: string;
  readonly type: string;
  readonly lastModified: number;
}

/** Persist `file`, in place of any document persisted before: its state follows with `persistState`. */
export async function persistFile(file: File): Promise<void> {
  // Read before the transaction: one waiting on anything else commits empty.
  const persistedFile: PersistedFile = {
    bytes: await file.arrayBuffer(),
    name: file.name,
    type: file.type,
    lastModified: file.lastModified,
  };
  await inStore("readwrite", (store) => {
    store.delete(STATE);
    return store.put(persistedFile, FILE);
  });
}

/** Persist what's true of the persisted file now: the server's reading of it, the work done to it, and until when. */
export async function persistState<Work>(state: Omit<Persisted<Work>, "file">): Promise<void> {
  await inStore("readwrite", (store) => store.put({ ...state, format: FORMAT }, STATE));
}

/** The document persisted here: none if there's none, or its hour has run out, or it was stored in another shape. */
export async function persistedDocument(): Promise<Persisted<unknown> | null> {
  const persistedFile = await inStore("readonly", (store) => store.get(FILE));
  const state = await inStore("readonly", (store) => store.get(STATE));
  const whole = persistedFile?.bytes instanceof ArrayBuffer && state?.format === FORMAT;
  if (whole && state.until > Date.now()) {
    const { bytes, name, type, lastModified } = persistedFile;
    return {
      file: new File([bytes], name, { type, lastModified }),
      doc: withInFile(renamed(state.doc)),
      work: state.work,
      until: state.until,
    };
  }
  // Past its hour, or half stored: nothing to open again, so nothing to keep.
  if (persistedFile !== undefined || state !== undefined) await forgetPersisted();
  return null;
}

/** A persisted reading of the document, its sentences as `sentences` though it was stored when they were `copy`. */
// Nothing is stored past the server's hour, so this can go once the rename has been deployed that long.
function renamed(doc: Document | (Omit<Document, "sentences"> & { copy: Document["sentences"] })): Document {
  if ("sentences" in doc) return doc;
  const { copy, ...rest } = doc;
  return { ...rest, sentences: copy };
}

// Every sentence the browser reads: typed, so one the server adds must be listed here.
const SENTENCES_READ: Record<keyof Sentences, true> = {
  missing: true,
  will_leave_out: true,
  too_long: true,
  substitute: true,
  substitute_same_widths: true,
  drawn_in_your_copy: true,
  drawn_in_own_copy: true,
  undo_redaction: true,
  reopened: true,
  export_left_out: true,
  page_left_out: true,
  page_put_back: true,
  form_field_not_edited: true,
  empty: true,
  edit_breaks_signature: true,
  back_to: true,
  changed_to: true,
  put_back: true,
  redacted: true,
  your_copy_in_use: true,
  your_copy_removed: true,
  warning_muted: true,
  warning_unmuted: true,
  downloaded: true,
  find_nothing: true,
  find_identical: true,
  find_different: true,
  find_places: true,
  find_some_different: true,
  changed_places: true,
  changes: true,
  changes_different: true,
  changes_similar: true,
  changes_some: true,
  changes_some_different: true,
  changes_some_similar: true,
  at_rest_different: true,
  at_rest_similar: true,
  changes_too_long: true,
  typed_identical: true,
  typed_different: true,
  typed_similar: true,
  join_parts: true,
  options: true,
  approximate: true,
  characters: true,
};

/** Whether a persisted reading of the document lacks a sentence this build reads: one persisted by an earlier build. */
export function lacksSentences({ sentences }: Pick<Document, "sentences">): boolean {
  return Object.keys(SENTENCES_READ).some((key) => !(key in sentences));
}

/** A persisted reading of the document, each span saying whether its file's copy draws it, though stored before they did. */
// Nothing is stored past the server's hour, so this can go once spans have said so that long.
function withInFile(
  doc: Omit<Document, "spans"> & { spans: (Omit<SpanInfo, "in_file"> & Partial<Pick<SpanInfo, "in_file">>)[] },
): Document {
  // Taken as drawn by its own copy only where nothing stands in for its font, as was read before.
  const spans = doc.spans.map((span) => ({ ...span, in_file: span.in_file ?? span.substitute === null }));
  return { ...doc, spans };
}

/** Forget the document persisted here, file and all. */
export async function forgetPersisted(): Promise<void> {
  await inStore("readwrite", (store) => store.clear());
}

/** Forget the document persisted here: null once it's gone, else why it's still here, in words for the user. */
export async function forgetOrSayWhy(name: string): Promise<string | null> {
  try {
    await forgetPersisted();
    return null;
  } catch (error) {
    // The browser's storage refused: a failure no server saw, so said in the browser's own words.
    if (error instanceof DOMException) {
      return `This browser wouldn't delete your changes to ${name}. Try again, or clear this site's data in the browser's settings.`;
    }
    return sentenceOf(error);
  }
}
