import { beforeAll, describe, expect, test } from "vitest";
import { type AmountValue, withAmount } from "./amounts";
import { learnEveryLanguage } from "./dateNames";
import { type DateReading, type DateValue, dateReadingOf, withDate } from "./dates";
import { editableValueIn, formattedValuesIn } from "./formattedValues";

// Every language Intl has dates for, learnt in the background as an editor would: the dates here need them.
beforeAll(() => learnEveryLanguage());

/** How the dates of a document of `texts` read, for a reader of English. */
function readingOf(...texts: string[]): DateReading {
  return dateReadingOf(texts, { readerLanguages: ["en-US"] });
}

/** The one value `text` holds, a date, read as the document of `texts` settles it. */
function dateIn(text: string, dates = readingOf(text)): DateValue {
  const [value, ...more] = formattedValuesIn(text, { dates });
  if (value?.kind !== "date" || more.length > 0) throw new Error(`not one date: ${text}`);
  return value;
}

/** The one value `text` holds, an amount. */
function amountIn(text: string): AmountValue {
  const [value, ...more] = formattedValuesIn(text, { dates: readingOf(text) });
  if (value?.kind !== "amount" || more.length > 0) throw new Error(`not one amount: ${text}`);
  return value;
}

describe("a date", () => {
  test("is found inside a sentence, and only its own letters", () => {
    const text = "Due 14 March 2026, net 30";
    const date = dateIn(text);
    expect(text.slice(date.start, date.end)).toBe("14 March 2026");
    expect(date.date).toEqual({ year: 2026, month: 3, day: 14 });
    expect(withDate(text, date, { year: 2026, month: 3, day: 16 })).toBe("Due 16 March 2026, net 30");
  });

  test("is written back as the page wrote it: the 2nd of April gains no zero", () => {
    const date = dateIn("14 March 2026");
    expect(withDate("14 March 2026", date, { year: 2026, month: 4, day: 2 })).toBe("2 April 2026");
  });

  test("keeps its language, whatever the reader's: a German month stays German", () => {
    // Swiss and Low German name March so too: the document's other months are German's alone.
    const date = dateIn("14. März 2026", readingOf("14. März 2026", "1. Februar 2026", "24. Dezember 2025"));
    expect(withDate("14. März 2026", date, { year: 2026, month: 2, day: 3 })).toBe("3. Februar 2026");
  });

  test("written in numbers that read both ways offers nothing alone, and is read day first beside one past 12", () => {
    expect(formattedValuesIn("03/04/2026", { dates: readingOf("03/04/2026") })).toEqual([]);
    const date = dateIn("03/04/2026", readingOf("03/04/2026", "Signed 25/12/2026"));
    expect(date.date).toEqual({ year: 2026, month: 4, day: 3 });
    // Its zeros are kept, as the page wrote them.
    expect(withDate("03/04/2026", date, { year: 2026, month: 5, day: 9 })).toBe("09/05/2026");
  });
});

describe("an amount", () => {
  test("keeps its separators and its sign's place", () => {
    const amount = amountIn("1.250,00 €");
    expect(amount.amount).toBe(1250);
    expect(withAmount("1.250,00 €", amount, 980.5)).toBe("980,50 €");
  });

  test("in brackets, a negative, keeps them", () => {
    const amount = amountIn("Balance ($1,250.00)");
    expect(amount.amount).toBe(-1250);
    expect(withAmount("Balance ($1,250.00)", amount, -2400)).toBe("Balance ($2,400.00)");
    // With no sign or code, the same digits are no amount.
    expect(formattedValuesIn("(1,250.00)", { dates: readingOf() })).toEqual([]);
  });

  test("keeps its no-break space, between its digits and before its sign", () => {
    const text = "1\u00a0250,00\u00a0€";
    const amount = amountIn(text);
    expect(amount.amount).toBe(1250);
    expect(withAmount(text, amount, 32500.75)).toBe("32\u00a0500,75\u00a0€");
  });

  test("is no amount without a currency's sign or code: a number alone may be a quantity", () => {
    expect(formattedValuesIn("1250", { dates: readingOf() })).toEqual([]);
  });
});

describe("the value an editor opens on", () => {
  test("is the one the press landed on, else the first; an email has no editor", () => {
    const text = "Paid €40 on 14 March 2026 to anna@halde.de";
    const dates = readingOf(text);
    expect(editableValueIn(text, { dates, at: text.indexOf("March") })?.kind).toBe("date");
    expect(editableValueIn(text, { dates, at: undefined })?.kind).toBe("amount");
    expect(editableValueIn("Write to anna@halde.de", { dates, at: 12 })).toBeUndefined();
  });
});
