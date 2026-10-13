// Opening a PDF: it goes to the server, which reads its text and answers with the document.
import { ProblemError, upload } from "../api/client";
import type { Document } from "../api/types";
import { reportBug } from "../bugs";

/** A file this browser opened, and the server's reading of it. The file stays, to open it again. */
export interface Opened {
  readonly file: File;
  readonly doc: Document;
}

/** The document, or why it didn't open, in words for the user. */
export type Opening = { opened: Opened } | { failed: string };

/** What the landing shows: waiting for a file, one on its way to the server, or why the last didn't open. */
export type Landing =
  | { kind: "waiting" }
  | { kind: "opening"; name: string; sent: number; total: number }
  | { kind: "failed"; detail: string };

export async function openFile(file: File, onProgress: (sent: number, total: number) => void): Promise<Opening> {
  try {
    return { opened: { file, doc: await upload(file, onProgress) } };
  } catch (error) {
    return { failed: error instanceof ProblemError ? error.problem.detail : reportBug(error) };
  }
}
