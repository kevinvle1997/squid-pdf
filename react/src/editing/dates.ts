// Dates in a span's text, read from the text alone, and written back as the page wrote them: the order of
// their parts, what's between them, their digits, and a month or weekday named in the language they're in.
import { caseOf, type DateName, type NameKind, type NameWord, nameFor, namesOf, wordsBetweenIn } from "./dateNames";
import type { Extent } from "./formattedValues";

/** A day of the Gregorian calendar, its month from 1. */
export interface CalendarDay {
  readonly year: number;
  readonly month: number;
  readonly day: number;
}

type DatePart = "day" | "month" | "year";

/** How the page wrote a date: its parts in order, what's between them, their digits, and its names. */
interface DateFormat {
  readonly order: readonly [DatePart, DatePart, DatePart];
  readonly between: readonly [string, string];
  readonly digits: { readonly day: 1 | 2; readonly month: 1 | 2; readonly year: 2 | 4 };
  readonly month: NameWord | null; // null: the month is a number
  readonly weekday: { readonly word: NameWord; readonly between: string } | null; // a weekday written before it
}

export interface DateValue extends Extent {
  readonly kind: "date";
  readonly date: CalendarDay;
  readonly format: DateFormat;
}

/** Which part of a numeric date a document writes first, settled by a date in it that reads only one way. */
export type DateOrder = "day-first" | "month-first";

/** What settles how a date reads: the document's other dates, then the reader's own languages. */
export interface DateReading {
  readonly order: DateOrder | undefined;
  readonly monthWords: readonly string[]; // the month names the document's dates are written with, in lower case
  readonly readerLanguages: readonly string[]; // the browser's, first the one it likes best
}

// Between a date's parts: spaces, a full stop, a comma, a slash or a dash; or a short word a language writes there.
const BETWEEN = String.raw`(?:[\s\u00a0\u202f.,/\-]{1,3}|[\s\u00a0\u202f]+\p{Ll}{1,3}[\s\u00a0\u202f]+)`;
const NAME = String.raw`\p{L}+\.?`;
const DAY = String.raw`\d{1,2}`;
const YEAR = String.raw`\d{4}|\d{2}`;
// A word before a date, perhaps its weekday, and what's between them: "Saturday, ".
const BEFORE = String.raw`(?:(\p{L}+\.?)(,?[\s\u00a0\u202f]+))?`;
const ALONE_BEFORE = String.raw`(?<![\p{L}\p{N}])`;
const ALONE_AFTER = String.raw`(?![\p{L}\p{N}])`;

/** The patterns of a date with its month named, each with its parts in the order they come. */
const NAMED: readonly { readonly order: DateFormat["order"]; readonly pattern: RegExp }[] = [
  { order: ["day", "month", "year"], pattern: named(DAY, NAME, YEAR) },
  { order: ["month", "day", "year"], pattern: named(NAME, DAY, YEAR) },
  { order: ["year", "month", "day"], pattern: named(String.raw`\d{4}`, NAME, DAY) },
];

function named(...parts: [string, string, string]): RegExp {
  const [first, second, third] = parts;
  return new RegExp(
    `${ALONE_BEFORE}${BEFORE}(${first})(${BETWEEN})(${second})(${BETWEEN})(${third})${ALONE_AFTER}`,
    "gu",
  );
}

// The same mark twice between numbers, none of them part of a longer run of numbers: 14.03.2026, 2026-03-14.
const NUMERIC = new RegExp(
  `(?<![\\p{L}\\p{N}])(?<!\\p{N}[./-])${BEFORE}(\\d{1,4})([./-])(\\d{1,2})\\4(\\d{1,4})(?![\\p{L}\\p{N}])(?![./-]\\p{N})`,
  "gu",
);

// A two digit year is the year nearest this one that ends in it: in 2026, 99 is 1999 and 50 is 2050.
const CENTURY = 100;

/** How the dates in `texts`, a document's, settle how one of them reads, and the reader's languages after them. */
export function dateReadingOf(
  texts: Iterable<string>,
  { readerLanguages }: { readerLanguages: readonly string[] },
): DateReading {
  const orders = new Set<DateOrder>();
  const monthWords = new Set<string>();
  for (const text of texts) {
    const numericOrders = readEach(text, {
      pattern: NUMERIC,
      read: ([, , , first = "", , second = "", last = ""]) =>
        first.length === 4 || !isYear(last) ? undefined : onlyOrderOf(Number(first), Number(second)),
    });
    for (const order of numericOrders) orders.add(order);
    for (const { order, pattern } of NAMED) {
      const words = readEach(text, {
        pattern,
        read: (match) => {
          const word = match[3 + order.indexOf("month") * 2]?.replace(/\.$/, "") ?? "";
          return namesOf(word, "month").length > 0 ? word.toLowerCase() : undefined;
        },
      });
      for (const word of words) monthWords.add(word);
    }
  }
  const [only] = orders;
  return { order: orders.size === 1 ? only : undefined, monthWords: [...monthWords], readerLanguages };
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

/** The dates in `text`, those with a month named and those in numbers, read as `reading` settles them. */
export function datesIn(text: string, reading: DateReading): DateValue[] {
  const named = NAMED.flatMap(({ order, pattern }) =>
    readEach(text, { pattern, read: (match) => namedDateOf(match, { order, reading }) }),
  );
  return [...named, ...readEach(text, { pattern: NUMERIC, read: (match) => numericDateOf(match, reading) })];
}

/**
 * What `read` makes of each match of `pattern` in `text`. A match it makes nothing of is tried again a letter
 * on, since a date may start inside it: "40 on 14" looks like one, and "14 March 2026" follows.
 */
function readEach<T>(
  text: string,
  { pattern, read }: { pattern: RegExp; read: (match: RegExpExecArray) => T | undefined },
): T[] {
  const found: T[] = [];
  const searching = new RegExp(pattern.source, pattern.flags);
  for (let match = searching.exec(text); match !== null; match = searching.exec(text)) {
    const value = read(match);
    if (value === undefined) searching.lastIndex = match.index + 1;
    else found.push(value);
  }
  return found;
}

/** What a date's pattern matched: the word before it and what's after that word, then its parts and between them. */
function partsOf(match: RegExpMatchArray) {
  const [whole, before = "", beforeSpace = "", first = "", between1 = "", second = "", between2 = "", third = ""] =
    match;
  return { whole, before, beforeSpace, parts: [first, second, third] as const, between: [between1, between2] as const };
}

/**
 * A date whose month is named, if the languages that name it, and its weekday before it, settle on one. A full
 * stop after the name is the month's own where a language writes one ("janv."), else what's after it.
 */
function namedDateOf(
  match: RegExpMatchArray,
  { order, reading }: { order: DateFormat["order"]; reading: DateReading },
): DateValue | undefined {
  const { whole, before, beforeSpace, parts, between } = partsOf(match);
  const at = order.indexOf("month");
  let monthWord = parts[at] ?? "";
  let betweenParts: [string, string] = [between[0], between[1]];
  if (namesOf(monthWord, "month").length === 0 && monthWord.endsWith(".")) {
    monthWord = monthWord.slice(0, -1);
    betweenParts = at === 0 ? [`.${betweenParts[0]}`, betweenParts[1]] : [betweenParts[0], `.${betweenParts[1]}`];
  }
  const monthOnly = settledNames({ month: monthWord, between: betweenParts }, reading);
  if (monthOnly?.month === undefined) return undefined;
  const value = { day: parts[order.indexOf("day")] ?? "", year: parts[order.indexOf("year")] ?? "" };
  const date = dayOf({ year: value.year, month: monthOnly.month.index, day: Number(value.day) });
  if (date === undefined) return undefined;
  const weekdayWord = isWeekdayOf(before, date) ? before : undefined;
  const words = { month: monthWord, weekday: weekdayWord, between: betweenParts };
  const settled = weekdayWord === undefined ? monthOnly : settledNames(words, reading);
  // A weekday no language settles on: written for another date, it would be a guess.
  if (settled?.month === undefined) return undefined;
  const format: DateFormat = {
    order,
    between: betweenParts,
    digits: { day: hasZero(value.day) ? 2 : 1, month: 1, year: digitsOf(value.year) },
    month: wordOf(settled.month, monthWord),
    weekday: settled.weekday === undefined ? null : { word: wordOf(settled.weekday, before), between: beforeSpace },
  };
  return located({ date, format }, { match, whole, before: weekdayWord === undefined ? before + beforeSpace : "" });
}

/** A date written in numbers alone: year first, or a day and month in the order they can only read in, or the document's. */
function numericDateOf(match: RegExpMatchArray, reading: DateReading): DateValue | undefined {
  const [whole, before = "", beforeSpace = "", first = "", mark = "", second = "", last = ""] = match;
  const yearFirst = first.length === 4 && last.length <= 2;
  if (!yearFirst && (first.length > 2 || !isYear(last))) return undefined;
  const order = yearFirst ? "year-first" : (onlyOrderOf(Number(first), Number(second)) ?? reading.order);
  // Read either way and nothing settles which: an editor that guessed would write a wrong date that looks right.
  if (order === undefined) return undefined;
  const parts = numericPartsOf(order, { first, second, last });
  const date = dayOf({ year: parts.year, month: Number(parts.month), day: Number(parts.day) });
  if (date === undefined) return undefined;
  const weekdayWord = isWeekdayOf(before, date) ? before : undefined;
  const settled = weekdayWord === undefined ? undefined : settledNames({ weekday: weekdayWord, between: [] }, reading);
  // A weekday no language settles on: written for another date, it would be a guess.
  if (weekdayWord !== undefined && settled === undefined) return undefined;
  const format: DateFormat = {
    order: parts.order,
    between: [mark, mark],
    digits: { day: digitCountOf(parts.day), month: digitCountOf(parts.month), year: digitsOf(parts.year) },
    month: null,
    weekday: settled?.weekday === undefined ? null : { word: wordOf(settled.weekday, before), between: beforeSpace },
  };
  return located({ date, format }, { match, whole, before: weekdayWord === undefined ? before + beforeSpace : "" });
}

/** A date read from `match`, placed where it is in its text: past the word before it, unless that's its weekday. */
function located(
  { date, format }: Pick<DateValue, "date" | "format">,
  { match, whole, before }: { match: RegExpMatchArray; whole: string; before: string },
): DateValue {
  const start = (match.index ?? 0) + before.length;
  return { kind: "date", date, format, start, end: (match.index ?? 0) + whole.length };
}

/** Which of a numeric date's three numbers is its day, its month and its year. */
function numericPartsOf(
  order: DateOrder | "year-first",
  { first, second, last }: { first: string; second: string; last: string },
): { day: string; month: string; year: string; order: DateFormat["order"] } {
  switch (order) {
    case "year-first":
      return { year: first, month: second, day: last, order: ["year", "month", "day"] };
    case "day-first":
      return { day: first, month: second, year: last, order: ["day", "month", "year"] };
    case "month-first":
      return { month: first, day: second, year: last, order: ["month", "day", "year"] };
    default:
      return order satisfies never;
  }
}

/** The day, if the calendar has it. */
function dayOf({ year, month, day }: { year: string; month: number; day: number }): CalendarDay | undefined {
  const fullYear = year.length === 2 ? nearestYearEndingIn(Number(year)) : Number(year);
  if (month < 1 || month > 12 || day < 1 || day > daysIn(fullYear, month)) return undefined;
  return { year: fullYear, month, day };
}

function nearestYearEndingIn(twoDigits: number, thisYear = new Date().getUTCFullYear()): number {
  const sameCentury = thisYear - (thisYear % CENTURY) + twoDigits;
  const candidates = [sameCentury - CENTURY, sameCentury, sameCentury + CENTURY];
  return candidates.reduce((best, year) => (Math.abs(year - thisYear) < Math.abs(best - thisYear) ? year : best));
}

/** How many days `month` of `year` has. */
export function daysIn(year: number, month: number): number {
  return new Date(Date.UTC(year, month, 0)).getUTCDate();
}

function hasZero(digits: string): boolean {
  return digits.length === 2 && digits.startsWith("0");
}

function digitCountOf(digits: string): 1 | 2 {
  return digits.length === 2 ? 2 : 1;
}

function digitsOf(year: string): 2 | 4 {
  return year.length === 2 ? 2 : 4;
}

function wordOf(name: DateName, written: string): NameWord {
  return { language: name.language, width: name.width, letterCase: caseOf(written, name.name) };
}

/** `text` with `value` changed to `date`, written as the page wrote the date it replaces. */
export function withDate(text: string, value: DateValue, date: CalendarDay): string {
  return text.slice(0, value.start) + writtenDate(date, value.format) + text.slice(value.end);
}

function writtenDate(date: CalendarDay, format: DateFormat): string {
  const { digits } = format;
  const year = digits.year === 2 ? String(date.year % CENTURY).padStart(2, "0") : String(date.year);
  const month =
    format.month === null ? String(date.month).padStart(digits.month, "0") : nameFor("month", date.month, format.month);
  const parts = { day: String(date.day).padStart(digits.day, "0"), month, year };
  const [first, second, third] = format.order;
  const weekday =
    format.weekday === null ? "" : nameFor("weekday", weekdayOf(date), format.weekday.word) + format.weekday.between;
  return weekday + parts[first] + format.between[0] + parts[second] + format.between[1] + parts[third];
}

/**
 * Whether `word` names the day of the week `date` falls on, in some language. Why: a word before a date that
 * names another day isn't its weekday, but a word that happens to be one in some other language.
 */
function isWeekdayOf(word: string, date: CalendarDay): boolean {
  return namesOf(word, "weekday").some((name) => name.index === weekdayOf(date));
}

/** The day of the week `date` falls on, from 0, Sunday. */
export function weekdayOf({ year, month, day }: CalendarDay): number {
  return new Date(Date.UTC(year, month - 1, day)).getUTCDay();
}

// ---- Which language a date's names are in ----

/** A language that names a date's words, and the name each word is in it. */
interface Named {
  readonly language: string;
  readonly month?: DateName;
  readonly weekday?: DateName;
}

/** A date's words: its month's and weekday's names as written, and what's between its parts. */
interface DateWords {
  readonly month?: string;
  readonly weekday?: string;
  readonly between: readonly string[];
}

/**
 * The names a date's words are, in the one language they settle on: every language that names them, if all
 * write every month and weekday alike; else those the document's other dates are written in; else those the
 * reader reads. Undefined where that leaves more than one way to write them, so no editor guesses.
 */
function settledNames(words: DateWords, reading: DateReading): Named | undefined {
  let languages = languagesNaming(words);
  if (languages.length === 0) return undefined;
  if (writeAlike(languages, words)) return languages[0];
  const inDocument = mostOf(languages, (named) => reading.monthWords.filter((word) => names(named, word)).length);
  if (inDocument.length > 0) languages = inDocument;
  if (writeAlike(languages, words)) return languages[0];
  return readersOf(languages, { words, reading });
}

/** The languages naming every word in `words`, each name in the case written where any language has it so. */
function languagesNaming(words: DateWords): Named[] {
  const kinds = (["month", "weekday"] as const).filter((kind) => words[kind] !== undefined);
  const byLanguage = new Map<string, Partial<Record<NameKind, DateName[]>>>();
  for (const kind of kinds) {
    for (const name of namesOf(words[kind] ?? "", kind)) {
      const found = byLanguage.get(name.language) ?? {};
      found[kind] = [...(found[kind] ?? []), name];
      byLanguage.set(name.language, found);
    }
  }
  const all = [...byLanguage].flatMap(([language, found]) => {
    if (!kinds.every((kind) => found[kind] !== undefined)) return [];
    const pick = (kind: NameKind) => bestOf(found[kind] ?? [], words[kind] ?? "");
    return [{ language, month: pick("month"), weekday: pick("weekday") }];
  });
  const withWordsBetween = all.filter((named) => writesBetween(named, words.between));
  const asWritten = withWordsBetween.filter((named) => kinds.every((kind) => named[kind]?.name === words[kind]));
  return asWritten.length > 0 ? asWritten : withWordsBetween;
}

/** Of one language's names for a word, the one written just so, else any; long before short. */
function bestOf(names: readonly DateName[], written: string): DateName | undefined {
  return names.find((name) => name.name === written) ?? names[0];
}

/** Whether `named`'s language writes the words between a date's parts, where the page wrote some ("de"). */
function writesBetween(named: Named, between: readonly string[]): boolean {
  const words = between.flatMap((part) => part.toLowerCase().match(/\p{L}+/gu) ?? []);
  if (words.length === 0) return true;
  const own = wordsBetweenIn(named.language, named.month?.width ?? "long");
  return words.every((word) => own.includes(word));
}

/** Whether every one of `languages` writes each month and weekday the way the others do, in the page's case. */
function writeAlike(languages: readonly Named[], words: DateWords): boolean {
  const kinds = (["month", "weekday"] as const).filter((kind) => words[kind] !== undefined);
  return kinds.every((kind) => {
    const count = kind === "month" ? 12 : 7;
    const first = kind === "month" ? 1 : 0;
    return Array.from({ length: count }, (_, at) => at + first).every((index) => {
      const written = new Set(languages.map((named) => writtenIn(named, { kind, index, words })));
      return written.size === 1;
    });
  });
}

function writtenIn(named: Named, { kind, index, words }: { kind: NameKind; index: number; words: DateWords }): string {
  const name = named[kind];
  if (name === undefined) return "";
  return nameFor(kind, index, wordOf(name, words[kind] ?? ""));
}

/** Whether `named`'s language names `word` a month. */
function names(named: Named, word: string): boolean {
  return namesOf(word, "month").some((name) => name.language === named.language);
}

/** Those of `items` that score highest by `score`, when any scores at all. */
function mostOf<T>(items: readonly T[], score: (item: T) => number): T[] {
  const scores = items.map(score);
  const best = Math.max(0, ...scores);
  return best === 0 ? [] : items.filter((_, at) => scores[at] === best);
}

/**
 * The language among `languages` that the reader reads, the first of theirs that one does: its form for the
 * reader's region, else its own, else any of its forms, if they write alike.
 */
function readersOf(
  languages: readonly Named[],
  { words, reading }: { words: DateWords; reading: DateReading },
): Named | undefined {
  const baseOf = (tag: string) => new Intl.Locale(tag).language;
  for (const tag of reading.readerLanguages) {
    const same = languages.filter((named) => baseOf(named.language) === baseOf(tag));
    if (same.length === 0) continue;
    const exactly = same.filter((named) => named.language.toLowerCase() === tag.toLowerCase());
    const plain = same.filter((named) => named.language === baseOf(tag));
    const settled = [exactly, plain, same].find((set) => set.length > 0 && writeAlike(set, words));
    return settled?.[0];
  }
  return undefined;
}
