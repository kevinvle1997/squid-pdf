// Paying for no document limit: going to Stripe's pages to pay or manage it, and coming back paid.
import { billingPortal, checkout } from "../api/client";
import type { PaymentPage } from "../api/types";
import { sentenceOf } from "../bugs";
import type { AccountAnswer, AccountAnswered } from "./account";
import { PAID_ASK_EVERY_MS, PAID_ASKS } from "./constants";

/** Where Stripe's checkout sends this browser back once it has paid, as the server tells Stripe. */
const PAID_SEARCH = "?billing=paid";
const DAY = new Intl.DateTimeFormat("en", { day: "numeric", month: "long" });

/** Going to one of Stripe's pages: asking the server where, or why it couldn't, in words for the user. */
export type Going = { kind: "idle" } | { kind: "under way" } | { kind: "failed"; detail: string };

/** Back from paying: waiting for Stripe's word to reach the server, the month paid, or still waiting after every ask. */
export type BackFromPaying = { kind: "waiting" } | { kind: "paid"; until: number } | { kind: "late" };

/** Stripe's page the server names, or why it couldn't, in words for the user. */
type PaymentPageAnswer = { kind: "page"; url: string } | Extract<Going, { kind: "failed" }>;

/** Stripe's checkout, where the account signed in pays monthly for no document limit. */
export function checkoutPage(): Promise<PaymentPageAnswer> {
  return stripePage(checkout);
}

/** Stripe's portal, where the account signed in changes its card, reads its invoices, or cancels. */
export function billingPortalPage(): Promise<PaymentPageAnswer> {
  return stripePage(billingPortal);
}

/** Leave the app for one of Stripe's pages, at `url`. */
export function goTo(url: string): void {
  window.location.assign(url);
}

/** Whether Stripe's checkout sent this browser here, paid. */
export function isBackFromPaying(search: string): boolean {
  return search === PAID_SEARCH;
}

/** The account as it reads once its month is paid, asked up to `asks` times, `everyMs` apart; the last read if it never does. */
export async function accountOncePaid(
  ask: () => Promise<AccountAnswered>,
  { asks = PAID_ASKS, everyMs = PAID_ASK_EVERY_MS }: { asks?: number; everyMs?: number } = {},
): Promise<AccountAnswered> {
  let answer = await ask();
  for (let asked = 1; asked < asks && paidUntil(answer) === null; asked += 1) {
    await new Promise((resolve) => setTimeout(resolve, everyMs));
    answer = await ask();
  }
  return answer;
}

/** What coming back from paying found: until when it's paid, or that Stripe's word hasn't reached the server yet. */
export function backFrom(answer: AccountAnswer): BackFromPaying {
  const until = paidUntil(answer);
  return until === null ? { kind: "late" } : { kind: "paid", until };
}

/** What paying came to, in words for the landing: why going to Stripe failed, or what coming back found. */
export function paymentSaid(going: Going, back: BackFromPaying | null): { sentence: string; failed: boolean } | null {
  if (going.kind === "failed") return { sentence: going.detail, failed: true };
  if (back === null) return null;
  if (back.kind === "waiting")
    return { sentence: "Paid. Your limit lifts as soon as Stripe confirms it.", failed: false };
  if (back.kind === "paid") {
    return {
      sentence: `Paid: your account has no document limit until ${DAY.format(back.until * 1000)}.`,
      failed: false,
    };
  }
  return {
    sentence: "Stripe has your payment, and your limit lifts once it confirms it: reload in a minute to see it.",
    failed: false,
  };
}

/** Until when the account signed in has paid for no document limit, in Unix seconds; null where it hasn't. */
export function paidUntil(answer: AccountAnswer): number | null {
  return answer.kind === "read" ? (answer.state.signed_in?.paid_until ?? null) : null;
}

/** Whether the account signed in may pay: the server takes payment, and it has a limit to lift. */
export function mayPay(answer: AccountAnswer): boolean {
  if (answer.kind !== "read" || !answer.state.takes_payment) return false;
  const signedIn = answer.state.signed_in;
  return signedIn !== null && !signedIn.is_unlimited && signedIn.paid_until === null;
}

/** Whether the account signed in has billing to manage in Stripe's portal: it has gone to pay before. */
export function mayManageBilling(answer: AccountAnswer): boolean {
  return answer.kind === "read" && answer.state.takes_payment && (answer.state.signed_in?.has_billing ?? false);
}

/** The page `call` hands back, or why not. */
async function stripePage(call: () => Promise<PaymentPage>): Promise<PaymentPageAnswer> {
  try {
    return { kind: "page", url: (await call()).url };
  } catch (error) {
    return { kind: "failed", detail: sentenceOf(error) };
  }
}
