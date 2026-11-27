import { type AmountValue, amountsIn } from "./amounts";
import { type DateFormat, type DateReading, type DateValue, datesIn } from "./dates";
import { runTypedInto } from "./lines";

export interface Extent {
  readonly start: number;
  readonly end: number;
}

export interface EmailValue extends Extent {
  readonly kind: "email";
}

export type FormattedValue = DateValue | AmountValue | EmailValue;

export type EditableValue = DateValue | AmountValue;

const EMAIL =
  /(?<![\p{L}\p{N}._%+-])[\p{L}\p{N}._%+-]+@[\p{L}\p{N}-]+(?:\.[\p{L}\p{N}-]+)*\.\p{L}{2,}(?![\p{L}\p{N}-])/gu;

export function formattedValuesIn(text: string, { dates }: { dates: DateReading | null }): FormattedValue[] {
  const found = [...(dates === null ? [] : datesIn(text, dates)), ...amountsIn(text), ...emailsIn(text)];
  found.sort((a, b) => a.start - b.start || b.end - a.end);
  const kept: FormattedValue[] = [];
  for (const value of found) {
    const last = kept.at(-1);
    if (last === undefined || value.start >= last.end) kept.push(value);
  }
  return kept;
}

export type HeldValue =
  | (Extent & { readonly kind: "amount" })
  | (Extent & { readonly kind: "date"; readonly format: DateFormat; readonly written: string });

/** Held first: the dates, read later, would otherwise swap the editor from under the user. */
export function editableValueIn(
  text: string,
  { dates, at, held }: { dates: DateReading | null; at: number | undefined; held: HeldValue | null },
): EditableValue | undefined {
  const editable = editableIn(text, { dates });
  const under = at === undefined ? undefined : editable.find((value) => value.start <= at && at <= value.end);
  return heldValueIn(text, { dates, held }) ?? under ?? editable[0];
}

export function runExtents(runs: readonly string[]): Extent[] {
  let start = 0;
  return runs.map((run) => {
    const extent = { start, end: start + run.length };
    start = extent.end;
    return extent;
  });
}

function editableInRuns(runs: readonly string[], { dates }: { dates: DateReading | null }): EditableValue[] {
  const extents = runExtents(runs);
  const within = ({ start, end }: Extent) => extents.some((run) => run.start <= start && end <= run.end);
  return editableIn(runs.join(""), { dates }).filter(within);
}

export function lineValueIn(
  runs: readonly string[],
  { dates, at, held }: { dates: DateReading | null; at: number | undefined; held: HeldValue | null },
): EditableValue | undefined {
  const editable = editableInRuns(runs, { dates });
  const under = at === undefined ? undefined : editable.find((value) => value.start <= at && at <= value.end);
  const kept = heldValueIn(runs.join(""), { dates, held });
  const inARun = kept && editable.find((value) => value.kind === kept.kind && overlap(value, kept));
  return (inARun && kept) ?? under ?? editable[0];
}

export function caretRunValueIn(
  runs: readonly string[],
  { dates, caret }: { dates: DateReading | null; caret: number },
): EditableValue | undefined {
  const run = runExtents(runs)[runTypedInto(runs, caret)];
  if (run === undefined) return undefined;
  const inRun = editableInRuns(runs, { dates }).filter((value) => run.start <= value.start && value.end <= run.end);
  return inRun.find((value) => value.start <= caret && caret <= value.end) ?? inRun[0];
}

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

export function heldThrough(held: HeldValue, { before, after }: { before: string; after: string }): HeldValue {
  const shortest = Math.min(before.length, after.length);
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

export function heldOf(value: EditableValue, text: string): HeldValue {
  const { start, end } = value;
  if (value.kind === "amount") return { kind: "amount", start, end };
  return { kind: "date", start, end, format: value.format, written: text.slice(start, end) };
}

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

export function emailCountIn(text: string): number {
  return emailsIn(text).length;
}
