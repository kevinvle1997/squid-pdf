import { describe, expect, test } from "vitest";
import { type AmountValue, amountsIn, withAmount } from "./amounts";

/** The one amount `text` holds. */
function amountIn(text: string): AmountValue {
  const [amount, ...more] = amountsIn(text);
  if (amount === undefined || more.length > 0) throw new Error(`not one amount: ${text}`);
  return amount;
}

describe("an amount", () => {
  test("set apart by a thin or a narrow space reads whole, and keeps it", () => {
    for (const space of ["\u2009", "\u202f"]) {
      const text = `1${space}250,00 €`;
      const amount = amountIn(text);
      expect(amount.amount).toBe(1250);
      expect(withAmount(text, amount, 32500.75)).toBe(`32${space}500,75 €`);
    }
  });

  test("a range is no amount", () => {
    expect(amountsIn("$10-$20")).toEqual([]);
    expect(amountsIn("$10 \u2013 $20")).toEqual([]);
    expect(amountsIn("10-20 €")).toEqual([]);
  });

  test("set apart as India sets it reads whole, and is written so", () => {
    const amount = amountIn("₹1,25,000.00");
    expect(amount.amount).toBe(125000);
    expect(withAmount("₹1,25,000.00", amount, 1234567.5)).toBe("₹12,34,567.50");
  });

  test("in a currency with three decimals, three digits after its mark are decimals", () => {
    const amount = amountIn("KWD 1.250");
    expect(amount.amount).toBe(1.25);
    expect(withAmount("KWD 1.250", amount, 2)).toBe("KWD 2.000");
  });

  test("a minus sign after it makes it negative", () => {
    expect(amountIn("1.250,00\u2212 €").amount).toBe(-1250);
  });
});
