import { terms } from "../api/client";
import type { Terms } from "../api/types";
import { sentenceOf } from "../bugs";

export const TERMS_PATH = "/terms";

export type TermsAnswer =
  | { kind: "asking" }
  | { kind: "none" }
  | { kind: "read"; terms: Terms }
  | { kind: "failed"; detail: string };

export async function termsAnswer(): Promise<TermsAnswer> {
  try {
    const read = await terms();
    return read === null ? { kind: "none" } : { kind: "read", terms: read };
  } catch (error) {
    return { kind: "failed", detail: sentenceOf(error) };
  }
}

export function isTermsPath(pathname: string): boolean {
  return pathname.replace(/\/+$/, "") === TERMS_PATH;
}

export function termsHrefOf(answer: TermsAnswer): string | null {
  return answer.kind === "read" ? TERMS_PATH : null;
}
