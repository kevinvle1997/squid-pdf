// Opening a PDF: it goes to the server, which reads its text and answers with the document.
import { upload } from "../api/client";
import type { Document } from "../api/types";
import { sentenceOf } from "../bugs";

/** A file this browser opened, and the server's reading of it. The file stays, to open it again. */
export interface Opened {
  readonly file: File;
  readonly doc: Document;
}

/** The document, or why it didn't open, in words for the user. */
export type Opening = { opened: Opened } | { failed: string };

/** A document kept in this browser, as the landing offers it: its name, and until when it's kept. */
export interface Offered {
  readonly name: string;
  readonly until: number;
}

/**
 * What the landing shows: waiting for a file, one on its way to the server, or why the last didn't open;
 * and while it waits, a document kept here to open again.
 */
export type Landing =
  | { kind: "waiting"; kept?: Offered | undefined }
  | { kind: "opening"; name: string; sent: number; total: number }
  | { kind: "failed"; detail: string };

export async function openFile(file: File, onProgress: (sent: number, total: number) => void): Promise<Opening> {
  try {
    return { opened: { file, doc: await upload(file, onProgress) } };
  } catch (error) {
    return { failed: sentenceOf(error) };
  }
}
