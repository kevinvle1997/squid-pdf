import { ProblemError, upload } from "../api/client";
import type { Document } from "../api/types";
import { sentenceOf } from "../bugs";

export interface Opened {
  readonly file: File;
  readonly doc: Document;
}

export type Landing =
  | { kind: "waiting" }
  | { kind: "opening"; name: string; sent: number; total: number }
  | { kind: "failed"; detail: string }
  | { kind: "month used"; detail: string };

export type SignInFirst = { kind: "sign in"; detail: string };

type Opening = { kind: "open"; opened: Opened } | Extract<Landing, { kind: "failed" | "month used" }> | SignInFirst;

export async function openFile(file: File, onProgress: (sent: number, total: number) => void): Promise<Opening> {
  try {
    return { kind: "open", opened: { file, doc: await upload(file, onProgress) } };
  } catch (error) {
    const said = error instanceof ProblemError ? error.problem : null;
    if (said?.type === "free_documents_used") return { kind: "sign in", detail: said.detail };
    if (said?.type === "monthly_documents_used") return { kind: "month used", detail: said.detail };
    return { kind: "failed", detail: sentenceOf(error) };
  }
}

export function shareSent({ sent, total }: { sent: number; total: number }): number | null {
  // All sent, the server is reading the text: a real step, not invented progress.
  if (total > 0 && sent >= total) return null;
  return total > 0 ? sent / total : 0;
}
