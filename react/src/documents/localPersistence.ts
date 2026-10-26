// A document persisted in this browser until the server's own hour runs out, so a reload or a closed tab
// loses no work. Files are stored as bytes: Safari's private windows, as WebKit's tests run, won't store a Blob.
import type { Document } from "../api/types";
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
      doc: renamed(state.doc),
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
