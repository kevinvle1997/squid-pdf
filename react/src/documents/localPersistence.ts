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
  readonly order: readonly number[]; // the pages its download has, in order, as the work leaves them: the first to load
}

/** The work persisted with a document, and until when: what an editor opened again restores. */
export type PersistedWork<Work> = Pick<Persisted<Work>, "work" | "until">;

// The names saved work is stored under: renaming one loses every user's saved work.
const DATABASE = "squid-pdf";
const STORE = "kept";
const FILE = "file";
const STATE = "state";
// The server's reading, apart from the work, as it's the most there is to store and changes least; and its hour.
const DOC = "doc";
const UNTIL = "until";
// What's stored changes shape with the code: one stored in another shape is forgotten, but for the shape before.
const FORMAT = 2;
// The shape before: the reading and its hour stored in the state, with the work.
const READING_IN_STATE = 1;

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
    for (const key of [STATE, DOC, UNTIL]) store.delete(key);
    return store.put(persistedFile, FILE);
  });
}

/**
 * Persist the work done to the persisted file, which document it was done on, its order and until when, in one
 * transaction with the server's reading of it once that changed: the most there is to store.
 */
export async function persistState<Work>(
  { doc, work, until, order }: Omit<Persisted<Work>, "file">,
  { docChanged = true }: { docChanged?: boolean } = {},
): Promise<void> {
  await inStore("readwrite", (store) => {
    if (docChanged) store.put(doc, DOC);
    store.put(until, UNTIL);
    return store.put({ work, docId: doc.id, order, format: FORMAT }, STATE);
  });
}

/** Persist until when the server keeps the persisted file, as each of its replies moves it: the work stays as it is. */
export async function persistUntil(until: number): Promise<void> {
  await inStore("readwrite", (store) => store.put(until, UNTIL));
}

/**
 * The document persisted here: none if there's none, or its hour has run out, or it isn't whole: stored in a shape but
 * this build's or the one before, or its work done on another document than the reading stored.
 */
export async function persistedDocument(): Promise<Persisted<unknown> | null> {
  const persistedFile = await inStore("readonly", (store) => store.get(FILE));
  const state = await inStore("readonly", (store) => store.get(STATE));
  const stored = await storedDoc(state);
  const whole = persistedFile?.bytes instanceof ArrayBuffer && stored !== null;
  if (whole && stored.until > Date.now()) {
    const { bytes, name, type, lastModified } = persistedFile;
    return {
      file: new File([bytes], name, { type, lastModified }),
      doc: withSpanFields(renamed(stored.doc)),
      work: state.work,
      until: stored.until,
      // Stored before the order was: the original's pages, which is where opening again starts all the same.
      order: state.order ?? stored.doc.pages.map((_, page) => page),
    };
  }
  // Past its hour, or half stored: nothing to open again, so nothing to keep.
  if (persistedFile !== undefined || state !== undefined) await forgetPersisted();
  return null;
}

/**
 * The server's reading persisted with `state`'s work, and its hour: none where either is missing, the reading is of
 * another document than the work (another tab's, opened since), or `state` is in another shape.
 */
async function storedDoc(
  state: { format?: unknown; doc?: Document; docId?: string; until?: number } | undefined,
): Promise<{ doc: Document; until: number } | null> {
  if (state?.format !== FORMAT && state?.format !== READING_IN_STATE) return null;
  const doc = await inStore<Document | undefined>("readonly", (store) => store.get(DOC));
  const until = await inStore<number | undefined>("readonly", (store) => store.get(UNTIL));
  // The build before kept both in the state, and a reply since may have moved the hour. Nothing outlives the
  // server's hour, so this can go an hour after a deploy.
  if (state.format === READING_IN_STATE) return wholeDoc(state.doc, until ?? state.until);
  return doc?.id === state.docId ? wholeDoc(doc, until) : null;
}

/** The reading and its hour, if both are there. */
function wholeDoc(doc: Document | undefined, until: unknown): { doc: Document; until: number } | null {
  return doc !== undefined && typeof until === "number" ? { doc, until } : null;
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
  not_in_font: true,
  will_leave_out: true,
  too_long: true,
  substitute: true,
  substitute_same_widths: true,
  drawn_in_your_copy: true,
  drawn_in_own_copy: true,
  undo_redaction: true,
  undo_redacted_words: true,
  reopened: true,
  export_left_out: true,
  page_left_out: true,
  page_put_back: true,
  how_to_fix: true,
  how_to_fix_touch: true,
  how_to_add: true,
  how_to_add_touch: true,
  new_text: true,
  new_text_moves: true,
  new_text_moves_touch: true,
  form_field_not_edited: true,
  empty: true,
  edit_breaks_signature: true,
  email_broken: true,
  back_to: true,
  changed_to: true,
  put_back: true,
  redacted: true,
  already_redacted: true,
  new_text_added: true,
  new_text_moved: true,
  new_text_taken_out: true,
  new_text_set_in: true,
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
  find_empty: true,
  find_some_empty: true,
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
  join_sentences: true,
  options: true,
  approximate: true,
  characters: true,
};

/** Whether a persisted reading of the document lacks a sentence this build reads: one persisted by an earlier build. */
export function lacksSentences({ sentences }: Pick<Document, "sentences">): boolean {
  return Object.keys(SENTENCES_READ).some((key) => !(key in sentences));
}

/**
 * A persisted reading of the document, each span saying whether its file's copy draws it and whether its line is turned,
 * though stored before they did.
 */
// Nothing is stored past the server's hour, so this can go once spans have said so that long.
function withSpanFields(
  doc: Omit<Document, "spans"> & {
    spans: (Omit<SpanInfo, "in_file" | "turned"> & Partial<Pick<SpanInfo, "in_file" | "turned">>)[];
  },
): Document {
  // Taken as drawn by its own copy only where nothing stands in for its font, as was read before; and as turned,
  // unknown, so what's redacted of it is drawn over all of it until the server says where.
  const spans = doc.spans.map((span) => ({
    ...span,
    in_file: span.in_file ?? span.substitute === null,
    turned: span.turned ?? true,
  }));
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
