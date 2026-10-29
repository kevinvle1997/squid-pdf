// The service's terms: asked of the server, which has them only where its deployment wrote them.
import { terms } from "../api/client";
import type { Terms } from "../api/types";
import { sentenceOf } from "../bugs";

/** Where the terms are read: a page of the app's own, which the landing links. */
export const TERMS_PATH = "/terms";

/** The terms: still being asked for, none in this deployment, read, or why they couldn't be. */
export type TermsAnswer =
  | { kind: "asking" }
  | { kind: "none" }
  | { kind: "read"; terms: Terms }
  | { kind: "failed"; detail: string };

/** What the server answers when asked for the terms, a failure included. */
export async function termsAnswer(): Promise<TermsAnswer> {
  try {
    const read = await terms();
    return read === null ? { kind: "none" } : { kind: "read", terms: read };
  } catch (error) {
    return { kind: "failed", detail: sentenceOf(error) };
  }
}

/** Whether `pathname` is the terms' page, a trailing slash or none. */
export function isTermsPath(pathname: string): boolean {
  return pathname.replace(/\/+$/, "") === TERMS_PATH;
}

/** Where the landing links the terms: once the server says its deployment has them, and only then. */
export function termsHrefOf(answer: TermsAnswer): string | null {
  return answer.kind === "read" ? TERMS_PATH : null;
}
