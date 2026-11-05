// Who this browser is signed in as, on a server that keeps accounts, and signing in by a link sent by email.
import { account, askForLink, signIn, signOut } from "../api/client";
import type { AccountState } from "../api/types";
import { sentenceOf } from "../bugs";

/** Where a sign-in link opens: a page of the app's own, its token after the `#`, which reaches no server's log. */
export const SIGN_IN_PATH = "/sign-in";

/** The account: still being asked for, read, or why it couldn't be. */
export type AccountAnswer =
  | { kind: "asking" }
  | { kind: "read"; state: AccountState }
  | { kind: "failed"; detail: string };

/** A sign-in under way: asking for a link, a link on its way, or why it went wrong, in the server's words. */
export type Signing =
  | { kind: "idle" }
  | { kind: "under way" }
  | { kind: "sent"; sentence: string }
  | { kind: "failed"; detail: string };

/** What the server answered about the account, a failure included. */
export type AccountAnswered = Exclude<AccountAnswer, { kind: "asking" }>;

/** What the server answers when asked who this browser is, a failure included. */
export function accountAnswer(): Promise<AccountAnswered> {
  return answered(account);
}

/** Ask for a link that signs in as `email`: the server's sentence saying where it went, or why it didn't. */
export async function linkAsked(email: string): Promise<Signing> {
  try {
    return { kind: "sent", sentence: (await askForLink(email)).sentence };
  } catch (error) {
    return { kind: "failed", detail: sentenceOf(error) };
  }
}

/** Trade a link's token for a sign-in: the account it signed in to, or why it didn't. */
export function signedInWith(token: string): Promise<AccountAnswered> {
  return answered(() => signIn(token));
}

/** Sign this browser out: the account as it now reads, or why it couldn't. */
export function signedOut(): Promise<AccountAnswered> {
  return answered(signOut);
}

/** The account `call` answers with, or why it didn't, in words for the user. */
async function answered(call: () => Promise<AccountState>): Promise<AccountAnswered> {
  try {
    return { kind: "read", state: await call() };
  } catch (error) {
    return { kind: "failed", detail: sentenceOf(error) };
  }
}

/** The account as the app starts: where the page is a sign-in link's, its token traded first, and why it failed. */
export async function accountAtStart(
  token: string | null,
): Promise<{ account: AccountAnswered; linkFailed: string | null }> {
  if (token === null) return { account: await accountAnswer(), linkFailed: null };
  const signedIn = await signedInWith(token);
  if (signedIn.kind === "read") return { account: signedIn, linkFailed: null };
  const account = await accountAnswer();
  // Signed in after all, as a reload that trades a link again is: the failure is no news.
  return { account, linkFailed: signedInAs(account) === null ? signedIn.detail : null };
}

/** The token a sign-in link carried, from the page's `#`; null where there's none. */
export function tokenIn(pathname: string, hash: string): string | null {
  if (pathname.replace(/\/+$/, "") !== SIGN_IN_PATH) return null;
  const token = hash.replace(/^#/, "");
  return token === "" ? null : token;
}

/** Whether this browser must sign in before it opens a document: only invited people may, and it isn't signed in. */
export function mustSignIn(answer: AccountAnswer): boolean {
  return answer.kind === "read" && answer.state.invite_only && answer.state.signed_in === null;
}

/** The address this browser is signed in as; null where it isn't, or the server keeps no accounts. */
export function signedInAs(answer: AccountAnswer): string | null {
  return answer.kind === "read" ? (answer.state.signed_in?.email ?? null) : null;
}
