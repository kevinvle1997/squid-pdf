// Dates, amounts and emails in a span's text, read from the text alone: a date or an amount to be written
// back as the page wrote it (dates.ts, amounts.ts), an email only to be checked as it's typed.
import { type AmountValue, amountsIn } from "./amounts";
import { type DateFormat, type DateReading, type DateValue, datesIn } from "./dates";

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

/**
 * Which value an editor is offered for, its kind and where it lies: held as the text changes, and the dates are
 * read. A date's also holds its form, and the words it was last read from or written as.
 */
export type HeldValue =
  | (Extent & { readonly kind: "amount" })
  | (Extent & { readonly kind: "date"; readonly format: DateFormat; readonly written: string });

/**
 * The value a date or amount editor opens on: the one `held` (`heldValueIn`), else the one at `at` in the text,
 * else the first. Why held first: the dates, read later, would otherwise swap the editor from under the user.
 */
export function editableValueIn(
  text: string,
  { dates, at, held }: { dates: DateReading | null; at: number | undefined; held: HeldValue | null },
): EditableValue | undefined {
  const editable = editableIn(text, { dates });
  const under = at === undefined ? undefined : editable.find((value) => value.start <= at && at <= value.end);
  return heldValueIn(text, { dates, held }) ?? under ?? editable[0];
}

/**
 * The value of `held`'s kind where it lies in `text`: none with none held, or once it's gone from the text. A date
 * still reading as it was written keeps the form held: "2 avril", read again, has lost the "1er" it was written for.
 */
export function heldValueIn(
  text: string,
  { dates, held }: { dates: DateReading | null; held: HeldValue | null },
): EditableValue | undefined {
  if (held === null) return undefined;
  const found = editableIn(text, { dates }).find((value) => value.kind === held.kind && overlap(value, held));
  const asWritten =
    found?.kind === "date" && held.kind === "date" && text.slice(found.start, found.end) === held.written;
  if (asWritten) return { ...found, format: held.format };
  return found;
}

function editableIn(text: string, { dates }: { dates: DateReading | null }): EditableValue[] {
  return formattedValuesIn(text, { dates }).filter((value) => value.kind !== "email");
}

/**
 * Where `held` lies once the text reads `after` for `before`: carried with the words either side of what changed,
 * so a paste or a deletion ahead of it, however long, moves it along. Typed right before it or right after, it keeps
 * to its own letters; rewritten inside, it covers what's there now.
 */
export function heldThrough(held: HeldValue, { before, after }: { before: string; after: string }): HeldValue {
  const shortest = Math.min(before.length, after.length);
  // The words the change left either side of it, at the start and at the end.
  let ahead = 0;
  while (ahead < shortest && before[ahead] === after[ahead]) ahead++;
  let behind = 0;
  while (behind < shortest - ahead && before.at(-1 - behind) === after.at(-1 - behind)) behind++;
  const changedUpTo = before.length - behind;
  const moved = after.length - before.length;
  const start = held.start >= changedUpTo ? held.start + moved : Math.min(held.start, ahead);
  const end = held.end <= ahead ? held.end : Math.max(held.end, changedUpTo) + moved;
  return { ...held, start, end };
}

/** What's held of `value`, read in `text`, while its editor is offered. */
export function heldOf(value: EditableValue, text: string): HeldValue {
  const { start, end } = value;
  if (value.kind === "amount") return { kind: "amount", start, end };
  return { kind: "date", start, end, format: value.format, written: text.slice(start, end) };
}

/** What's held of `value` once its editor has written over it, the text reading `after` for `before`. */
export function heldWritten(value: EditableValue, { before, after }: { before: string; after: string }): HeldValue {
  return heldOf({ ...value, end: value.end + after.length - before.length }, after);
}

function overlap(a: Extent, b: Extent): boolean {
  return a.start <= b.end && b.start <= a.end;
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
