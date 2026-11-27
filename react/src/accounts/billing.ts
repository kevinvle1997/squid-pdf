import { billingPortal, checkout } from "../api/client";
import type { PaymentPage } from "../api/types";
import { sentenceOf } from "../bugs";
import type { AccountAnswer, AccountAnswered } from "./account";
import { PAID_ASK_EVERY_MS, PAID_ASKS } from "./constants";

const PAID_SEARCH = "?billing=paid";
const DAY = new Intl.DateTimeFormat("en", { day: "numeric", month: "long" });

export type Going = { kind: "idle" } | { kind: "under way" } | { kind: "failed"; detail: string };

export type BackFromPaying = { kind: "waiting" } | { kind: "paid"; until: number } | { kind: "late" };

type PaymentPageAnswer = { kind: "page"; url: string } | Extract<Going, { kind: "failed" }>;

export function checkoutPage(): Promise<PaymentPageAnswer> {
  return stripePage(checkout);
}

export function billingPortalPage(): Promise<PaymentPageAnswer> {
  return stripePage(billingPortal);
}

export function goTo(url: string): void {
  window.location.assign(url);
}

export function isBackFromPaying(search: string): boolean {
  return search === PAID_SEARCH;
}

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

export function backFrom(answer: AccountAnswer): BackFromPaying {
  const until = paidUntil(answer);
  return until === null ? { kind: "late" } : { kind: "paid", until };
}

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

export function paidUntil(answer: AccountAnswer): number | null {
  return answer.kind === "read" ? (answer.state.signed_in?.paid_until ?? null) : null;
}

export function mayPay(answer: AccountAnswer): boolean {
  if (answer.kind !== "read" || !answer.state.takes_payment) return false;
  const signedIn = answer.state.signed_in;
  return signedIn !== null && !signedIn.is_unlimited && signedIn.paid_until === null;
}

export function mayManageBilling(answer: AccountAnswer): boolean {
  return answer.kind === "read" && answer.state.takes_payment && (answer.state.signed_in?.has_billing ?? false);
}

async function stripePage(call: () => Promise<PaymentPage>): Promise<PaymentPageAnswer> {
  try {
    return { kind: "page", url: (await call()).url };
  } catch (error) {
    return { kind: "failed", detail: sentenceOf(error) };
  }
}
