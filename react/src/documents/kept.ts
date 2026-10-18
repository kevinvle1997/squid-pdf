// A document kept in this browser until the server's own hour runs out, so a reload or a closed tab loses
// no work. Files are kept as bytes: Safari's private windows, as WebKit's tests run, won't keep a Blob.
import type { Document } from "../api/types";

/** What's kept of a document: the file, the server's reading of it, the work done to it, and until when. */
export interface Kept<Work> {
  readonly file: File;
  readonly doc: Document;
  readonly work: Work;
  readonly until: number; // when the server forgets it too, in ms since the epoch
}

/** The work kept with a document, and until when: what an editor opened again takes up. */
export type KeptWork<Work> = Pick<Kept<Work>, "work" | "until">;

const DATABASE = "squid-pdf";
const STORE = "kept";
const FILE = "file";
const STATE = "state";
// What's kept changes shape with the code that keeps it: one kept by another shape is forgotten.
const FORMAT = 1;

// One connection for the page's life, opened on first use.
let connection: Promise<IDBDatabase> | undefined;

/** The browser's database the document is kept in. */
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

/** A file as it's kept: its bytes, and what makes them a file again. */
interface KeptFile {
  readonly bytes: ArrayBuffer;
  readonly name: string;
  readonly type: string;
  readonly lastModified: number;
}

/** Keep `file`, in place of any document kept before: its state follows with `keepState`. */
export async function keepFile(file: File): Promise<void> {
  // Read before the transaction: one waiting on anything else commits empty.
  const keptFile: KeptFile = {
    bytes: await file.arrayBuffer(),
    name: file.name,
    type: file.type,
    lastModified: file.lastModified,
  };
  await inStore("readwrite", (store) => {
    store.delete(STATE);
    return store.put(keptFile, FILE);
  });
}

/** Keep what's true of the kept file now: the server's reading of it, the work done to it, and until when. */
export async function keepState<Work>(state: Omit<Kept<Work>, "file">): Promise<void> {
  await inStore("readwrite", (store) => store.put({ ...state, format: FORMAT }, STATE));
}

/** The document kept here: none if there's none, or its hour has run out, or it was kept in another shape. */
export async function keptDocument(): Promise<Kept<unknown> | null> {
  const keptFile = await inStore("readonly", (store) => store.get(FILE));
  const state = await inStore("readonly", (store) => store.get(STATE));
  const whole = keptFile?.bytes instanceof ArrayBuffer && state?.format === FORMAT;
  if (whole && state.until > Date.now()) {
    const { bytes, name, type, lastModified } = keptFile;
    return {
      file: new File([bytes], name, { type, lastModified }),
      doc: state.doc,
      work: state.work,
      until: state.until,
    };
  }
  // Past its hour, or half kept: nothing to open again, so nothing to keep.
  if (keptFile !== undefined || state !== undefined) await forgetKept();
  return null;
}

/** Forget the document kept here, file and all. */
export async function forgetKept(): Promise<void> {
  await inStore("readwrite", (store) => store.clear());
}
