// Files are stored as bytes: Safari's private windows, as WebKit's tests run, won't store a Blob.
import type { Document, Sentences, SpanInfo } from "../api/types";
import { sentenceOf } from "../bugs";

export interface Persisted<Work> {
  readonly file: File;
  readonly doc: Document;
  readonly work: Work;
  readonly until: number;
}

export type PersistedWork<Work> = Pick<Persisted<Work>, "work" | "until">;

// Renaming one loses every user's saved work.
const DATABASE = "squid-pdf";
const STORE = "kept";
const FILE = "file";
const STATE = "state";
const FORMAT = 1;

let connection: Promise<IDBDatabase> | undefined;

function database(): Promise<IDBDatabase> {
  connection ??= new Promise((resolve, reject) => {
    const request = indexedDB.open(DATABASE, 1);
    request.onupgradeneeded = () => request.result.createObjectStore(STORE);
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  return connection;
}

async function inStore<T>(mode: IDBTransactionMode, use: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const transaction = (await database()).transaction(STORE, mode);
  const request = use(transaction.objectStore(STORE));
  return new Promise((resolve, reject) => {
    transaction.oncomplete = () => resolve(request.result);
    transaction.onerror = () => reject(transaction.error);
    transaction.onabort = () => reject(transaction.error);
  });
}

interface PersistedFile {
  readonly bytes: ArrayBuffer;
  readonly name: string;
  readonly type: string;
  readonly lastModified: number;
}

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

export async function persistState<Work>(state: Omit<Persisted<Work>, "file">): Promise<void> {
  await inStore("readwrite", (store) => store.put({ ...state, format: FORMAT }, STATE));
}

export async function persistedDocument(): Promise<Persisted<unknown> | null> {
  const persistedFile = await inStore("readonly", (store) => store.get(FILE));
  const state = await inStore("readonly", (store) => store.get(STATE));
  const whole = persistedFile?.bytes instanceof ArrayBuffer && state?.format === FORMAT;
  if (whole && state.until > Date.now()) {
    const { bytes, name, type, lastModified } = persistedFile;
    return {
      file: new File([bytes], name, { type, lastModified }),
      doc: withSpanFields(withLines(renamed(state.doc))),
      work: state.work,
      until: state.until,
    };
  }
  if (persistedFile !== undefined || state !== undefined) await forgetPersisted();
  return null;
}

function renamed(doc: Document | (Omit<Document, "sentences"> & { copy: Document["sentences"] })): Document {
  if ("sentences" in doc) return doc;
  const { copy, ...rest } = doc;
  return { ...rest, sentences: copy };
}

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
  split_made: true,
  split_joined: true,
  split_changed: true,
  split_cant_here: true,
  split_redacted: true,
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

export function lacksSentences({ sentences }: Pick<Document, "sentences">): boolean {
  return Object.keys(SENTENCES_READ).some((key) => !(key in sentences));
}

function withSpanFields(
  doc: Omit<Document, "spans"> & {
    spans: (Omit<SpanInfo, "in_file" | "turned"> & Partial<Pick<SpanInfo, "in_file" | "turned">>)[];
  },
): Document {
  // As turned, so what's redacted of it is drawn over all of it until the server says where.
  const spans = doc.spans.map((span) => ({
    ...span,
    in_file: span.in_file ?? span.substitute === null,
    turned: span.turned ?? true,
  }));
  return { ...doc, spans };
}

function withLines(doc: Omit<Document, "lines"> & Partial<Pick<Document, "lines">>): Document {
  return { ...doc, lines: doc.lines ?? [] };
}

export async function forgetPersisted(): Promise<void> {
  await inStore("readwrite", (store) => store.clear());
}

export async function forgetOrSayWhy(name: string): Promise<string | null> {
  try {
    await forgetPersisted();
    return null;
  } catch (error) {
    if (error instanceof DOMException) {
      return `This browser wouldn't delete your changes to ${name}. Try again, or clear this site's data in the browser's settings.`;
    }
    return sentenceOf(error);
  }
}
