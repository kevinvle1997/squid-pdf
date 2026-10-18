// Opening a PDF: it goes to the server, which reads its text and answers with the document.
import { upload } from "../api/client";
import type { Document } from "../api/types";
import { sentenceOf } from "../bugs";

/** A file this browser opened, and the server's reading of it. The file stays, to open it again. */
export interface Opened {
  readonly file: File;
  readonly doc: Document;
}

/** What the landing shows: waiting for a file, one on its way to the server, or why the last didn't open. */
export type Landing =
  | { kind: "waiting" }
  | { kind: "opening"; name: string; sent: number; total: number }
  | { kind: "failed"; detail: string };

/** The document opened, or why it didn't, in words for the user. */
type Opening = { kind: "open"; opened: Opened } | Extract<Landing, { kind: "failed" }>;

/** Send `file` to the server, which reads its text: the document opened, or why it didn't. */
export async function openFile(file: File, onProgress: (sent: number, total: number) => void): Promise<Opening> {
  try {
    return { kind: "open", opened: { file, doc: await upload(file, onProgress) } };
  } catch (error) {
    return { kind: "failed", detail: sentenceOf(error) };
  }
}

/** How much of a file on its way is sent, a share of it: none once it's all sent, as the server reads it. */
export function shareSent({ sent, total }: { sent: number; total: number }): number | null {
  // All sent, the server is reading the text: a real step, not invented progress.
  if (total > 0 && sent >= total) return null;
  return total > 0 ? sent / total : 0;
}
