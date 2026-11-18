import { describe, expect, test, vi } from "vitest";
import { anAccount, aSignedIn } from "../fixtures";
import type { AccountAnswered } from "./account";
import { accountOncePaid, isBackFromPaying, mayManageBilling, mayPay, paymentSaid } from "./billing";

const PAID_UNTIL = Date.UTC(2026, 11, 18) / 1000;

/** The account as the server reads it, signed in, paid or not. */
function read(fields: Parameters<typeof aSignedIn>[0] = {}, takes_payment = true): AccountAnswered {
  return { kind: "read", state: anAccount({ takes_payment, signed_in: aSignedIn(fields) }) };
}

describe("accountOncePaid", () => {
  test("asks again until the account reads as paid, then stops", async () => {
    const ask = vi
      .fn<() => Promise<AccountAnswered>>()
      .mockResolvedValueOnce(read())
      .mockResolvedValueOnce(read())
      .mockResolvedValue(read({ paid_until: PAID_UNTIL }));
    const paid = await accountOncePaid(ask, { asks: 5, everyMs: 0 });
    expect(ask).toHaveBeenCalledTimes(3);
    expect(paid).toEqual(read({ paid_until: PAID_UNTIL }));
  });

  test("gives up after its asks, with the last read", async () => {
    const ask = vi.fn<() => Promise<AccountAnswered>>().mockResolvedValue(read());
    expect(await accountOncePaid(ask, { asks: 3, everyMs: 0 })).toEqual(read());
    expect(ask).toHaveBeenCalledTimes(3);
  });
});

test("an account may pay only where the server takes payment, and it has a limit to lift", () => {
  expect(mayPay(read())).toBe(true);
  expect(mayPay(read({}, false))).toBe(false);
  expect(mayPay(read({ is_unlimited: true }))).toBe(false);
  expect(mayPay(read({ paid_until: PAID_UNTIL }))).toBe(false);
  expect(mayPay({ kind: "read", state: anAccount({ takes_payment: true }) })).toBe(false);
  expect(mayPay({ kind: "asking" })).toBe(false);
});

test("billing is managed only by an account that has gone to pay, where the server takes payment", () => {
  expect(mayManageBilling(read({ has_billing: true }))).toBe(true);
  expect(mayManageBilling(read())).toBe(false);
  expect(mayManageBilling(read({ has_billing: true }, false))).toBe(false);
});

test("Stripe's checkout sends the browser back to one address alone", () => {
  expect(isBackFromPaying("?billing=paid")).toBe(true);
  expect(isBackFromPaying("")).toBe(false);
  expect(isBackFromPaying("?billing=cancelled")).toBe(false);
});

test("what paying came to: a failure in the server's words, or what coming back found", () => {
  const idle = { kind: "idle" } as const;
  expect(paymentSaid({ kind: "failed", detail: "Payments can't be reached." }, null)).toEqual({
    sentence: "Payments can't be reached.",
    failed: true,
  });
  expect(paymentSaid(idle, null)).toBeNull();
  expect(paymentSaid(idle, { kind: "paid", until: PAID_UNTIL })?.sentence).toBe(
    "Paid: your account has no document limit until December 18.",
  );
  expect(paymentSaid(idle, { kind: "late" })?.failed).toBe(false);
});
