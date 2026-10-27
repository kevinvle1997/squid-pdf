// Dates, amounts and email addresses in a span's text, read from the text alone, and a date or an
// amount written back as the page wrote it: its order, separators, zeros, month name and language.
import { caseOf, type MonthWord, monthNamed, monthNameFor } from "./monthNames";

/** Where a formatted value is in its text, in UTF-16 units as `slice` takes them. */
interface Place {
  readonly start: number;
  readonly end: number;
}

/** A day of the Gregorian calendar, its month from 1. */
export interface CalendarDay {
  readonly year: number;
  readonly month: number;
  readonly day: number;
}

type DatePart = "day" | "month" | "year";

/** How the page wrote a date: its parts in order, what's between them, its zeros, its year's digits, its month. */
interface DateFormat {
  readonly order: readonly [DatePart, DatePart, DatePart];
  readonly between: readonly [string, string];
  readonly zeros: boolean; // a day or month under 10 written with a zero before it
  readonly yearDigits: 2 | 4;
  readonly monthWord: MonthWord | null; // null: the month is a number
}

/** How the page wrote an amount: what's round its number, and how the number's digits are set. */
interface AmountFormat {
  readonly before: string; // its sign or code and the space after it, when it comes first
  readonly after: string; // the space and its sign or code, when it comes last
  readonly code: string | undefined; // its currency's code, where Intl knows one for it
  readonly group: string | null; // between each three digits, if the page set them apart
  readonly decimal: string; // before the decimals
  readonly decimals: number;
  readonly negative: Negative | null; // how it wrote a negative amount, if it was one
}

/** The marks a negative amount takes: a minus or brackets round it, a minus after its sign, or after its number. */
interface Negative {
  readonly open: string;
  readonly afterSign: string;
  readonly afterNumber: string;
  readonly close: string;
}

export interface DateValue extends Place {
  readonly kind: "date";
  readonly date: CalendarDay;
  readonly format: DateFormat;
}

export interface AmountValue extends Place {
  readonly kind: "amount";
  readonly amount: number;
  readonly format: AmountFormat;
}

export interface EmailValue extends Place {
  readonly kind: "email";
}

/** A date, an amount or an email address in a span's text. */
export type FormattedValue = DateValue | AmountValue | EmailValue;

/** A value with an editor of its own: an email address has only a check. */
export type EditableValue = DateValue | AmountValue;

/** Which part of a numeric date a document writes first, settled by a date in it that reads only one way. */
export type DateOrder = "day-first" | "month-first";

// Between a date's parts: spaces, a no-break one too, a full stop, a comma, a slash or a dash.
const BETWEEN = String.raw`[\s\u00a0\u202f.,/\-]{1,3}`;
const WORD = String.raw`\p{L}+\.?`;
const YEAR = String.raw`\d{4}|\d{2}`;
const ALONE_BEFORE = String.raw`(?<![\p{L}\p{N}])`;
const ALONE_AFTER = String.raw`(?![\p{L}\p{N}])`;

const DAY_THEN_NAME = new RegExp(
  `${ALONE_BEFORE}(\\d{1,2})(${BETWEEN})(${WORD})(${BETWEEN})(${YEAR})${ALONE_AFTER}`,
  "gu",
);
const NAME_THEN_DAY = new RegExp(
  `${ALONE_BEFORE}(${WORD})(${BETWEEN})(\\d{1,2})(${BETWEEN})(${YEAR})${ALONE_AFTER}`,
  "gu",
);
// The same mark twice between numbers, none of them part of a longer run of numbers: 14.03.2026, 2026-03-14.
const NUMERIC = /(?<![\p{L}\p{N}])(?<!\p{N}[./-])(\d{1,4})([./-])(\d{1,2})\2(\d{1,4})(?![\p{L}\p{N}])(?![./-]\p{N})/gu;

// An address, its name and domain of the letters addresses take, its last part letters alone.
const EMAIL =
  /(?<![\p{L}\p{N}._%+-])[\p{L}\p{N}._%+-]+@[\p{L}\p{N}-]+(?:\.[\p{L}\p{N}-]+)*\.\p{L}{2,}(?![\p{L}\p{N}-])/gu;

const SPACE = "[ \\u00a0\\u202f]";
const SIGN_OR_CODE = String.raw`\p{Sc}|[A-Z]{3}`;
// Three digits a group past the first, or none; then decimals. Which mark is which is read after.
const NUMBER = String.raw`\d{1,3}(?:[.,'\u2019 \u00a0\u202f]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?`;
const AMOUNT = new RegExp(
  `${ALONE_BEFORE}(?<open>[(\\-\\u2212])?(?:(?<lead>${SIGN_OR_CODE})(?<leadSpace>${SPACE}?))?` +
    `(?<afterSign>[\\-\\u2212])?(?<number>${NUMBER})(?<afterNumber>-)?` +
    `(?:(?<trailSpace>${SPACE}?)(?<trail>${SIGN_OR_CODE}))?(?<close>\\))?${ALONE_AFTER}`,
  "gu",
);

/** The dates, amounts and email addresses in `text`, left to right; a numeric date that reads both ways only in `dateOrder`. */
export function formattedValuesIn(text: string, { dateOrder }: { dateOrder: DateOrder | undefined }): FormattedValue[] {
  const found = [...namedDatesIn(text), ...numericDatesIn(text, dateOrder), ...amountsIn(text), ...emailsIn(text)];
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
  { dateOrder, at }: { dateOrder: DateOrder | undefined; at: number | undefined },
): EditableValue | undefined {
  const editable = formattedValuesIn(text, { dateOrder }).filter((value) => value.kind !== "email");
  const under = at === undefined ? undefined : editable.find((value) => value.start <= at && at <= value.end);
  return under ?? editable[0];
}

/** Which part the numeric dates in `texts` write first, where one that reads only one way says, and none says otherwise. */
export function dateOrderIn(texts: Iterable<string>): DateOrder | undefined {
  const orders = new Set<DateOrder>();
  for (const text of texts) {
    for (const [, first = "", , second = "", last = ""] of text.matchAll(NUMERIC)) {
      if (first.length === 4 || !isYear(last)) continue;
      const order = onlyOrderOf(Number(first), Number(second));
      if (order !== undefined) orders.add(order);
    }
  }
  const [only] = orders;
  return orders.size === 1 ? only : undefined;
}

/** The order two numbers of a date read in when only one of them can be a month. */
function onlyOrderOf(first: number, second: number): DateOrder | undefined {
  if (first > 12 && second <= 12) return "day-first";
  if (second > 12 && first <= 12) return "month-first";
  return undefined;
}

function isYear(digits: string): boolean {
  return digits.length === 2 || digits.length === 4;
}

/** Dates written with their month's name, day first or month first. */
function namedDatesIn(text: string): DateValue[] {
  const dates: DateValue[] = [];
  for (const match of text.matchAll(DAY_THEN_NAME)) {
    const [whole, day = "", first = "", word = "", second = "", year = ""] = match;
    const date = namedDateOf({ whole, day, word, year, between: [first, second], order: ["day", "month", "year"] });
    if (date !== undefined) dates.push({ ...date, start: match.index, end: match.index + whole.length });
  }
  for (const match of text.matchAll(NAME_THEN_DAY)) {
    const [whole, word = "", first = "", day = "", second = "", year = ""] = match;
    const date = namedDateOf({ whole, day, word, year, between: [first, second], order: ["month", "day", "year"] });
    if (date !== undefined) dates.push({ ...date, start: match.index, end: match.index + whole.length });
  }
  return dates;
}

/**
 * A date whose month is `word`, if it names one in a language Intl knows. A full stop after it is the
 * month's own where the language writes one ("janv."), else what's between it and the next part.
 */
function namedDateOf(written: {
  whole: string;
  day: string;
  word: string;
  year: string;
  between: [string, string];
  order: DateFormat["order"];
}): Omit<DateValue, "start" | "end"> | undefined {
  const { whole, day, year, order } = written;
  let { word, between } = written;
  const shape = { whole, day: Number(day), year };
  let month = monthNamed(word, shape);
  if (month === undefined && word.endsWith(".")) {
    word = word.slice(0, -1);
    between = order[0] === "month" ? [`.${between[0]}`, between[1]] : [between[0], `.${between[1]}`];
    month = monthNamed(word, shape);
  }
  if (month === undefined) return undefined;
  const date = dayOf({ year, month: month.month, day: Number(day) });
  if (date === undefined) return undefined;
  const monthWord = { language: month.language, width: month.width, letterCase: caseOf(word, month.name) };
  const format: DateFormat = { order, between, zeros: hasZero(day), yearDigits: digitsOf(year), monthWord };
  return { kind: "date", date, format };
}

/** Dates written in numbers alone: year first, or a day and month in the order they can only read in, or the document's. */
function numericDatesIn(text: string, dateOrder: DateOrder | undefined): DateValue[] {
  const dates: DateValue[] = [];
  for (const match of text.matchAll(NUMERIC)) {
    const [whole, first = "", mark = "", second = "", last = ""] = match;
    const yearFirst = first.length === 4 && last.length <= 2;
    if (!yearFirst && (first.length > 2 || !isYear(last))) continue;
    const order = yearFirst ? "year-first" : (onlyOrderOf(Number(first), Number(second)) ?? dateOrder);
    // Read either way and nothing settles which: an editor that guessed would write a wrong date that looks right.
    if (order === undefined) continue;
    const parts = numericPartsOf(order, { first, second, last });
    const date = dayOf({ year: parts.year, month: Number(parts.month), day: Number(parts.day) });
    if (date === undefined) continue;
    const format: DateFormat = {
      order: parts.order,
      between: [mark, mark],
      zeros: hasZero(parts.day) || hasZero(parts.month),
      yearDigits: digitsOf(parts.year),
      monthWord: null,
    };
    dates.push({ kind: "date", date, format, start: match.index, end: match.index + whole.length });
  }
  return dates;
}

/** Which of a numeric date's three numbers is its day, its month and its year. */
function numericPartsOf(
  order: DateOrder | "year-first",
  { first, second, last }: { first: string; second: string; last: string },
): { day: string; month: string; year: string; order: DateFormat["order"] } {
  if (order === "year-first") return { year: first, month: second, day: last, order: ["year", "month", "day"] };
  if (order === "day-first") return { day: first, month: second, year: last, order: ["day", "month", "year"] };
  return { month: first, day: second, year: last, order: ["month", "day", "year"] };
}

/** The day, if the calendar has it; a two digit year is this century's. */
function dayOf({ year, month, day }: { year: string; month: number; day: number }): CalendarDay | undefined {
  const fullYear = year.length === 2 ? 2000 + Number(year) : Number(year);
  if (month < 1 || month > 12 || day < 1 || day > daysIn(fullYear, month)) return undefined;
  return { year: fullYear, month, day };
}

/** How many days `month` of `year` has. */
export function daysIn(year: number, month: number): number {
  return new Date(Date.UTC(year, month, 0)).getUTCDate();
}

function hasZero(digits: string): boolean {
  return digits.length === 2 && digits.startsWith("0");
}

function digitsOf(year: string): 2 | 4 {
  return year.length === 2 ? 2 : 4;
}

/** `text` with `value` changed to `date`, written as the page wrote the date it replaces. */
export function withDate(text: string, value: DateValue, date: CalendarDay): string {
  return text.slice(0, value.start) + writtenDate(date, value.format) + text.slice(value.end);
}

function writtenDate(date: CalendarDay, format: DateFormat): string {
  const pad = (number: number) => (format.zeros ? String(number).padStart(2, "0") : String(number));
  const year = format.yearDigits === 2 ? String(date.year % 100).padStart(2, "0") : String(date.year);
  const month = format.monthWord === null ? pad(date.month) : monthNameFor(date.month, format.monthWord);
  const parts = { day: pad(date.day), month, year };
  const [first, second, third] = format.order;
  return parts[first] + format.between[0] + parts[second] + format.between[1] + parts[third];
}

let currencies: ReadonlySet<string> | undefined;

/** The currency codes Intl knows. */
function currencyCodes(): ReadonlySet<string> {
  currencies ??= new Set(Intl.supportedValuesOf("currency"));
  return currencies;
}

/** Amounts with a currency's sign or code: a number alone may be a quantity or a reference. */
function amountsIn(text: string): AmountValue[] {
  const amounts: AmountValue[] = [];
  for (const match of text.matchAll(AMOUNT)) {
    const amount = amountOf(match);
    if (amount !== undefined) amounts.push(amount);
  }
  return amounts;
}

function amountOf(match: RegExpExecArray | RegExpMatchArray): AmountValue | undefined {
  const parts = match.groups ?? {};
  const [lead, trail] = [parts.lead, parts.trail];
  const signOrCode = lead ?? trail;
  if ((lead === undefined) === (trail === undefined) || signOrCode === undefined) return undefined;
  if (/^[A-Z]{3}$/.test(signOrCode) && !currencyCodes().has(signOrCode)) return undefined;
  const number = numberOf(parts.number ?? "");
  if (number === undefined) return undefined;
  let start = match.index ?? 0;
  let end = start + match[0].length;
  let open = parts.open ?? "";
  let close = parts.close ?? "";
  // A bracket only one side is the sentence's, not the amount's.
  if (open === "(" && close === "") [open, start] = ["", start + 1];
  if (close === ")" && open !== "(") [close, end] = ["", end - 1];
  const negative = { open, afterSign: parts.afterSign ?? "", afterNumber: parts.afterNumber ?? "", close };
  const isNegative = Object.values(negative).some((mark) => mark !== "");
  const format: AmountFormat = {
    before: lead === undefined ? "" : lead + (parts.leadSpace ?? ""),
    after: trail === undefined ? "" : (parts.trailSpace ?? "") + trail,
    code: currencyCodeOf(signOrCode),
    group: number.group,
    decimal: number.decimal,
    decimals: number.decimals,
    negative: isNegative ? negative : null,
  };
  return { kind: "amount", amount: isNegative ? -number.value : number.value, format, start, end };
}

const DECIMAL_MARKS = new Set([".", ","]);

/** A number's value and how its digits are set: the last mark is its decimals', unless three digits follow it as a group's. */
function numberOf(
  written: string,
): { value: number; group: string | null; decimal: string; decimals: number } | undefined {
  const digits = (part: string) => part.replace(/\D/g, "");
  const marks = [...written.matchAll(/\D/g)];
  const last = marks.at(-1);
  if (last === undefined) return { value: Number(written), group: null, decimal: ".", decimals: 0 };
  const lastMark = last[0];
  const oneKind = marks.every((found) => found[0] === lastMark);
  // Every mark a group's: one kind, three digits after the last, or a mark decimals are never written after.
  if (oneKind && (written.length - last.index - 1 === 3 || !DECIMAL_MARKS.has(lastMark))) {
    return { value: Number(digits(written)), group: lastMark, decimal: lastMark === "," ? "." : ",", decimals: 0 };
  }
  const groupMarks = new Set(marks.slice(0, -1).map((found) => found[0]));
  const [group = null] = groupMarks;
  if (!DECIMAL_MARKS.has(lastMark) || groupMarks.size > 1 || group === lastMark) return undefined;
  const fraction = written.slice(last.index + 1);
  const value = Number(`${digits(written.slice(0, last.index))}.${fraction}`);
  return { value, group, decimal: lastMark, decimals: fraction.length };
}

/** `text` with `value` changed to `amount`, written as the page wrote the amount it replaces. */
export function withAmount(text: string, value: AmountValue, amount: number): string {
  return text.slice(0, value.start) + writtenAmount(amount, value.format) + text.slice(value.end);
}

// The minus a negative takes where the page wrote none.
const MINUS: Negative = { open: "-", afterSign: "", afterNumber: "", close: "" };

function writtenAmount(amount: number, format: AmountFormat): string {
  const scaled = String(Math.round(Math.abs(amount) * 10 ** format.decimals)).padStart(format.decimals + 1, "0");
  const whole = scaled.slice(0, scaled.length - format.decimals);
  const fraction = scaled.slice(scaled.length - format.decimals);
  const grouped = format.group === null ? whole : whole.replace(/\B(?=(\d{3})+$)/g, format.group);
  const number = format.decimals === 0 ? grouped : grouped + format.decimal + fraction;
  const marks = amount < 0 ? (format.negative ?? MINUS) : null;
  return (
    (marks?.open ?? "") +
    format.before +
    (marks?.afterSign ?? "") +
    number +
    (marks?.afterNumber ?? "") +
    format.after +
    (marks?.close ?? "")
  );
}

let codesBySign: ReadonlyMap<string, string> | undefined;

/** The code of the currency `signOrCode` is: itself, or the one whose sign it is, as Intl writes signs in English. */
function currencyCodeOf(signOrCode: string): string | undefined {
  if (currencyCodes().has(signOrCode)) return signOrCode;
  if (codesBySign === undefined) {
    const bySign = new Map<string, string>();
    for (const code of currencyCodes()) {
      const format = new Intl.NumberFormat("en", { style: "currency", currency: code });
      const sign = format.formatToParts(1).find((part) => part.type === "currency")?.value;
      if (sign !== undefined && !bySign.has(sign)) bySign.set(sign, code);
    }
    codesBySign = bySign;
  }
  return codesBySign.get(signOrCode);
}

function emailsIn(text: string): EmailValue[] {
  return [...text.matchAll(EMAIL)].map((match) => ({
    kind: "email",
    start: match.index,
    end: match.index + match[0].length,
  }));
}

/** How many email addresses `text` reads as. */
export function addressCountIn(text: string): number {
  return emailsIn(text).length;
}
