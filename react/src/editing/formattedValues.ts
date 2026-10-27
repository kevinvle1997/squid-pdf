// Dates, amounts and emails in a span's text, read from the text alone: a date or an amount to be written
// back as the page wrote it (dates.ts, amounts.ts), an email only to be checked as it's typed.
import { type AmountValue, amountsIn } from "./amounts";
import { type DateReading, type DateValue, datesIn } from "./dates";

/** Where a formatted value lies in its text, in UTF-16 units as `slice` takes them. */
export interface Extent {
  readonly start: number;
  readonly end: number;
}

export interface EmailValue extends Extent {
  readonly kind: "email";
}

/** A date, an amount or an email in a span's text. */
export type FormattedValue = DateValue | AmountValue | EmailValue;

/** A value with an editor of its own: an email has only a check. */
export type EditableValue = DateValue | AmountValue;

// An email, its name and domain of the letters emails take, its last part letters alone.
const EMAIL =
  /(?<![\p{L}\p{N}._%+-])[\p{L}\p{N}._%+-]+@[\p{L}\p{N}-]+(?:\.[\p{L}\p{N}-]+)*\.\p{L}{2,}(?![\p{L}\p{N}-])/gu;

/**
 * The dates, amounts and emails in `text`, left to right; a date read as `dates` settles it, or not at all, and
 * none while the document's dates aren't read yet (null), which an amount or an email never waits on.
 */
export function formattedValuesIn(text: string, { dates }: { dates: DateReading | null }): FormattedValue[] {
  const found = [...(dates === null ? [] : datesIn(text, dates)), ...amountsIn(text), ...emailsIn(text)];
  // A date first where two read the same letters: what's found earliest, then longest, keeps them.
  found.sort((a, b) => a.start - b.start || b.end - a.end);
  const kept: FormattedValue[] = [];
  for (const value of found) {
    const last = kept.at(-1);
    if (last === undefined || value.start >= last.end) kept.push(value);
  }
  return kept;
}

/** The value a date or amount editor opens on: the one at `at` in the text, else the first. */
export function editableValueIn(
  text: string,
  { dates, at }: { dates: DateReading | null; at: number | undefined },
): EditableValue | undefined {
  const editable = formattedValuesIn(text, { dates }).filter((value) => value.kind !== "email");
  const under = at === undefined ? undefined : editable.find((value) => value.start <= at && at <= value.end);
  return under ?? editable[0];
}

function emailsIn(text: string): EmailValue[] {
  return [...text.matchAll(EMAIL)].map((match) => ({
    kind: "email",
    start: match.index,
    end: match.index + match[0].length,
  }));
}

/** How many emails `text` reads as. */
export function emailCountIn(text: string): number {
  return emailsIn(text).length;
}
