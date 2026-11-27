import { account, askForLink, signIn, signOut } from "../api/client";
import type { AccountState, DocumentsLeft } from "../api/types";
import { sentenceOf } from "../bugs";

/** The token goes after the `#`, which reaches no server's log. */
export const SIGN_IN_PATH = "/sign-in";

export type AccountAnswer =
  | { kind: "asking" }
  | { kind: "read"; state: AccountState }
  | { kind: "failed"; detail: string };

export type Signing =
  | { kind: "idle" }
  | { kind: "under way" }
  | { kind: "sent"; sentence: string }
  | { kind: "failed"; detail: string };

export type AccountAnswered = Exclude<AccountAnswer, { kind: "asking" }>;

export function accountAnswer(): Promise<AccountAnswered> {
  return answered(account);
}

export async function linkAsked(email: string): Promise<Signing> {
  try {
    return { kind: "sent", sentence: (await askForLink(email)).sentence };
  } catch (error) {
    return { kind: "failed", detail: sentenceOf(error) };
  }
}

export function signedInWith(token: string): Promise<AccountAnswered> {
  return answered(() => signIn(token));
}

export function signedOut(): Promise<AccountAnswered> {
  return answered(signOut);
}

async function answered(call: () => Promise<AccountState>): Promise<AccountAnswered> {
  try {
    return { kind: "read", state: await call() };
  } catch (error) {
    return { kind: "failed", detail: sentenceOf(error) };
  }
}

export async function accountAtStart(
  token: string | null,
): Promise<{ account: AccountAnswered; linkFailed: string | null }> {
  if (token === null) return { account: await accountAnswer(), linkFailed: null };
  const signedIn = await signedInWith(token);
  if (signedIn.kind === "read") return { account: signedIn, linkFailed: null };
  const account = await accountAnswer();
  // A reload that trades a link again is signed in after all: the failure is no news.
  return { account, linkFailed: signedInAs(account) === null ? signedIn.detail : null };
}

export function tokenIn(pathname: string, hash: string): string | null {
  if (pathname.replace(/\/+$/, "") !== SIGN_IN_PATH) return null;
  const token = hash.replace(/^#/, "");
  return token === "" ? null : token;
}

export function mustSignIn(answer: AccountAnswer): boolean {
  return answer.kind === "read" && answer.state.invite_only && answer.state.signed_in === null;
}

export function documentsLeft(answer: AccountAnswer): DocumentsLeft | null {
  return answer.kind === "read" ? (answer.state.signed_in?.documents_left ?? null) : null;
}

export function signedInAs(answer: AccountAnswer): string | null {
  return answer.kind === "read" ? (answer.state.signed_in?.email ?? null) : null;
}
