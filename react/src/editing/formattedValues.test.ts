import { describe, expect, test, vi } from "vitest";
import { renderStrips } from "../api/client";
import type { Render } from "../api/types";
import { aDoc, aFont, aSpan, halfWideGlyphs, SENTENCES } from "../fixtures";
import { createEditor } from "./editor";
import {
  type AmountValue,
  type DateValue,
  dateOrderIn,
  formattedValuesIn,
  withAmount,
  withDate,
} from "./formattedValues";
import { edit } from "./typing";
import { markedDays } from "./valueEditor";
import { fill } from "./words";

vi.mock(import("../api/client"), async (original) => ({ ...(await original()), renderStrips: vi.fn() }));

const UNSETTLED = { dateOrder: undefined };

/** The one value `text` holds, a date, numeric ones read in `dateOrder`. */
function dateIn(text: string, dateOrder = dateOrderIn([text])): DateValue {
  const [value, ...more] = formattedValuesIn(text, { dateOrder });
  if (value?.kind !== "date" || more.length > 0) throw new Error(`not one date: ${text}`);
  return value;
}

/** The one value `text` holds, an amount. */
function amountIn(text: string): AmountValue {
  const [value, ...more] = formattedValuesIn(text, UNSETTLED);
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
    const date = dateIn("14. März 2026");
    expect(withDate("14. März 2026", date, { year: 2026, month: 2, day: 3 })).toBe("3. Februar 2026");
  });

  test("written in numbers that read both ways offers nothing alone, and is read day first beside one past 12", () => {
    expect(formattedValuesIn("03/04/2026", { dateOrder: dateOrderIn(["03/04/2026"]) })).toEqual([]);
    const dateOrder = dateOrderIn(["03/04/2026", "Signed 25/12/2026"]);
    const date = dateIn("03/04/2026", dateOrder);
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
    expect(formattedValuesIn("(1,250.00)", UNSETTLED)).toEqual([]);
  });

  test("keeps its no-break space, between its digits and before its sign", () => {
    const text = "1\u00a0250,00\u00a0€";
    const amount = amountIn(text);
    expect(amount.amount).toBe(1250);
    expect(withAmount(text, amount, 32500.75)).toBe("32\u00a0500,75\u00a0€");
  });

  test("is no amount without a currency's sign or code: a number alone may be a quantity", () => {
    expect(formattedValuesIn("1250", UNSETTLED)).toEqual([]);
  });
});

describe("a day in the calendar", () => {
  test("is marked when its date needs a digit or letter the span's font lacks, with the server's sentence", () => {
    vi.mocked(renderStrips).mockReturnValue(new Promise<Render>(() => undefined));
    // A subset that drew only "Due 14 March 2026": no 5 and no 7.
    const glyphs = halfWideGlyphs("Due 14 March 2026");
    const span = aSpan({ id: "due", text: "Due 14 March 2026", room_pt: 100 });
    const doc = aDoc({ spans: [span], fonts: [aFont("Times-Roman", { glyphs })] });
    const editor = createEditor(new File(["%PDF-"], "invoice.pdf"), doc, 2);
    edit(editor, "due", null);
    const date = dateIn(span.text);

    const marked = markedDays(editor.store.get(), { value: date, text: span.text, year: 2026, month: 3 });
    const noSeven = fill(SENTENCES.missing, { chars: ["7"], font: "Liberation Serif Regular" });
    expect(marked.get(17)).toBe(noSeven);
    expect(marked.get(15)).toBe(fill(SENTENCES.missing, { chars: ["5"], font: "Liberation Serif Regular" }));
    // The days it can write are left unmarked.
    expect(marked.has(14)).toBe(false);
    expect(marked.has(16)).toBe(false);
  });
});
