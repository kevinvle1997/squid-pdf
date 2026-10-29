// Dates in a span's text, read from the text alone, and written back as the page wrote them: the order of
// their parts, what's between them, their digits, and a month, weekday or ordinal in the language they're in.
import { caseOf, type DateName, type NameKind, type NameWord, nameFor, namesOf, wordsBetweenIn } from "./dateNames";
import type { Extent } from "./formattedValues";
import {
  type DayOrdinal,
  type OrdinalWord,
  ordinalWordOf,
  someLanguageWrites,
  suffixFor,
  writesOrdinal,
} from "./ordinals";

/** A day of the Gregorian calendar, its month from 1. */
export interface CalendarDay {
  readonly year: number;
  readonly month: number;
  readonly day: number;
}

type DatePart = "day" | "month" | "year";

/** How the page wrote a date: its parts in order, what's between them and after them, their digits, and its words. */
export interface DateFormat {
  readonly order: readonly [DatePart, DatePart, DatePart];
  readonly between: readonly [string, string];
  readonly closing: string; // after its last part: "日" in 2026年3月14日
  readonly digits: { readonly day: 1 | 2; readonly month: 1 | 2; readonly year: 2 | 4 };
  readonly month: NameWord | null; // null: the month is a number
  readonly ordinal: OrdinalWord | null; // null: the day is a number alone
  readonly weekday: WeekdayFormat | null;
}

/** A weekday written beside a date: its name, which side of the date, and what's written either side of the name. */
interface WeekdayFormat {
  readonly word: NameWord;
  readonly side: "before" | "after";
  readonly around: readonly [string, string];
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
// A day, perhaps an ordinal: 14th.
const DAY = String.raw`\d{1,2}\p{L}{0,2}`;
const YEAR = String.raw`\d{4}|\d{2}`;
// A word before a date, perhaps its weekday, and what's between them: "Saturday, ".
const BEFORE = String.raw`(?:(\p{L}+\.?)(,?[\s\u00a0\u202f]+))?`;
const ALONE_BEFORE = String.raw`(?<![\p{L}\p{N}])`;
const ALONE_AFTER = String.raw`(?![\p{L}\p{N}])`;
// Not run on into more numbers: "Lun 16/03" is no date in "Lun 16/03/2026".
const NO_MORE_NUMBERS = String.raw`(?![./-]\p{N})`;
// A range's first day and what joins it to the next, ending the text before a date: "14-", "14th – ", "14 and ".
// The day starts the text or follows a space, never a mark or a letter: "1.2 – ", "#12 - ", "10:30 – ", "v2 – ".
const RANGE_DAY_BEFORE = /(?:^|\s)\d{1,2}(?:\p{L}{1,2}|\.)?(?:\s*[-–\u2014‐‑/&]\s*|\s+(?:and|to)\s+)$/u;
// The word that ends a text, and a full stop after it: "Rev." in "Rev.", "held" in "is held".
const WORD_ENDING = /(\p{L}+)(\.?)$/u;
// Words in lower case that number what follows them, as "of" in "Page 1 of 3", "de" in "página 1 de 3".
const NUMBERING_WORDS = new Set(["of", "de", "di", "du", "sur", "von", "van", "no", "nr", "nº"]);
// The word after a date: "March" after "14 March - 15", which makes 15 a range's last day, and no year.
const WORD_AFTER = /^\s*(\p{L}+)/u;
// A four digit year after a date: "15, 2026" after "March 14", which makes 15 a range's last day.
const YEAR_AFTER = /^,?\s*\d{4}(?!\p{N})/u;

/** The patterns of a date with its month named, each with its parts in the order they come. */
const NAMED: readonly { readonly order: DateFormat["order"]; readonly pattern: RegExp }[] = [
  { order: ["day", "month", "year"], pattern: named(DAY, NAME, YEAR) },
  { order: ["month", "day", "year"], pattern: named(NAME, DAY, YEAR) },
  { order: ["year", "month", "day"], pattern: named(String.raw`\d{4}`, NAME, DAY) },
];

function named(...parts: [string, string, string]): RegExp {
  const [first, second, third] = parts;
  return new RegExp(
    `${ALONE_BEFORE}${BEFORE}(${first})(${BETWEEN})(${second})(${BETWEEN})(${third})${ALONE_AFTER}${NO_MORE_NUMBERS}`,
    "gu",
  );
}

// The same mark twice between numbers, none of them part of a longer run of numbers: 14.03.2026, 2026-03-14.
const NUMERIC = new RegExp(
  `(?<![\\p{L}\\p{N}])(?<!\\p{N}[./-])${BEFORE}(\\d{1,4})([./-])(\\d{1,2})\\4(\\d{1,4})(?![\\p{L}\\p{N}])(?![./-]\\p{N})`,
  "gu",
);

// A date with CJK's marks after its year, month and day: 2026年3月14日, 2026년 3월 14일. Its year in four digits,
// since an era's year, 令和8年, is no year of the Gregorian calendar.
const CJK = (
  [
    ["年", "月", "日"],
    ["년", "월", "일"],
  ] as const
).map(
  ([year, month, day]) =>
    new RegExp(String.raw`(?<!\p{N})(\d{4})(\s*${year}\s*)(\d{1,2})(\s*${month}\s*)(\d{1,2})(\s*${day})`, "gu"),
);
// After a CJK date: spaces, perhaps a bracket, then letters and a full stop, and the bracket's close.
const AFTER_CJK = /^(\s*)([(（[［【]?)(\p{L}+)(\.?)([)）\]］】]?)/u;
// A letter of a CJK script: a weekday in them runs on into the words after it, as "土曜日まで" does.
const CJK_LETTER = /^[\p{sc=Han}\p{sc=Hiragana}\p{sc=Katakana}\p{sc=Hangul}]/u;
// After a CJK date, a range's joiner and its last day: "～15日", "から15日". Not "～2026年3月20日", a date of its
// own, nor "- 10:00", a time.
const CJK_RANGE_AFTER = /^\s*(?:[～~〜\-–‐\u2014]|から)\s*(?!\d{4}\s*[年년])\d{1,2}(?![\d:])/u;

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
        read: (match) => monthWordIn(match, order),
      });
      for (const word of words) monthWords.add(word);
    }
  }
  const [only] = orders;
  return { order: orders.size === 1 ? only : undefined, monthWords: [...monthWords], readerLanguages };
}

/** The month a date's pattern matched, in lower case, where it names one and its day is written as a language does. */
function monthWordIn(match: RegExpMatchArray, order: DateFormat["order"]): string | undefined {
  const { parts } = partsOf(match);
  const word = parts[order.indexOf("month")]?.replace(/\.$/, "") ?? "";
  const { ordinal } = writtenDayOf(parts[order.indexOf("day")] ?? "");
  const named = namesOf(word, "month").length > 0 && (ordinal === undefined || someLanguageWrites(ordinal));
  return named ? word.toLowerCase() : undefined;
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

/** The dates in `text`, with a month named, in numbers or in CJK's marks, read as `reading` settles them. */
export function datesIn(text: string, reading: DateReading): DateValue[] {
  const named = NAMED.flatMap(({ order, pattern }) =>
    readEach(text, { pattern, read: (match) => namedDateOf(match, { order, reading }) }),
  );
  const cjk = CJK.flatMap((pattern) => readEach(text, { pattern, read: (match) => cjkDateOf(match, reading) }));
  return [...named, ...readEach(text, { pattern: NUMERIC, read: (match) => numericDateOf(match, reading) }), ...cjk];
}

/** What `read` makes of a date whose words nothing settles: no date, and none inside it, as the date alone. */
const UNSETTLED = Symbol("unsettled");

/**
 * What `read` makes of each match of `pattern` in `text`. A match it makes nothing of is tried again a letter
 * on, since a date may start inside it: "40 on 14" looks like one, and "14 March 2026" follows.
 */
function readEach<T>(
  text: string,
  { pattern, read }: { pattern: RegExp; read: (match: RegExpExecArray) => T | undefined | typeof UNSETTLED },
): T[] {
  const found: T[] = [];
  const searching = new RegExp(pattern.source, pattern.flags);
  for (let match = searching.exec(text); match !== null; match = searching.exec(text)) {
    const value = read(match);
    if (value === undefined) searching.lastIndex = match.index + 1;
    else if (value !== UNSETTLED) found.push(value);
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
): DateValue | undefined | typeof UNSETTLED {
  const { whole, before, beforeSpace, parts, between } = partsOf(match);
  const at = order.indexOf("month");
  let monthWord = parts[at] ?? "";
  let betweenParts: [string, string] = [between[0], between[1]];
  if (namesOf(monthWord, "month").length === 0 && monthWord.endsWith(".")) {
    monthWord = monthWord.slice(0, -1);
    betweenParts = at === 0 ? [`.${betweenParts[0]}`, betweenParts[1]] : [betweenParts[0], `.${betweenParts[1]}`];
  }
  const day = writtenDayOf(parts[order.indexOf("day")] ?? "");
  const monthOnlyWords = { month: monthWord, between: betweenParts, ordinal: day.ordinal };
  const monthOnly = settledNames(monthOnlyWords, reading);
  if (monthOnly?.month === undefined) return undefined;
  const year = parts[order.indexOf("year")] ?? "";
  const date = dayOf({ year, month: monthOnly.month.index, day: Number(day.digits) });
  if (date === undefined) return undefined;
  const weekday = asWeekday(before, date);
  // A range of days: an editor would rewrite only one end of it.
  if (inDayRange(match, { order, date, weekday })) return UNSETTLED;
  const settled = weekday === undefined ? monthOnly : settledNames({ ...monthOnlyWords, weekday }, reading);
  // The month settles alone, but not with the weekday before it.
  if (settled?.month === undefined) return UNSETTLED;
  const format: DateFormat = {
    order,
    between: betweenParts,
    closing: "",
    digits: { day: hasZero(day.digits) ? 2 : 1, month: 1, year: digitsOf(year) },
    month: wordOf(settled.month, monthWord),
    ordinal: day.ordinal === undefined ? null : ordinalWordOf(settled.language, day.ordinal),
    weekday: settled.weekday === undefined ? null : weekdayBeforeOf(settled.weekday, { before, beforeSpace }),
  };
  return located({ date, format }, { match, whole, before: weekday === undefined ? before + beforeSpace : "" });
}

/** Whether a named date's match is one end of a range of days: "14-15 March", "Fri 13 – Sat 14 March", "March 14/15". */
function inDayRange(
  match: RegExpMatchArray,
  { order, date, weekday }: { order: DateFormat["order"]; date: CalendarDay; weekday: WeekdayWord | undefined },
): boolean {
  const { whole, before, beforeSpace, parts, between } = partsOf(match);
  const text = match.input ?? "";
  const at = match.index ?? 0;
  if (order[0] === "month") return endsInDayRange({ parts, between, after: text.slice(at + whole.length) });
  if (order[0] !== "day") return false;
  const dayAt = at + (weekday === undefined ? before.length + beforeSpace.length : 0);
  return followsRangeDay(text.slice(0, dayAt), date) || yearIsNextDay({ parts, after: text.slice(at + whole.length) });
}

/**
 * Whether a date with its month first ends in a range's last day, read as a two digit year: "March 14-15, 2026",
 * "March 14, 15, 2026". Joined by dashes throughout and with no year after, "Mar-14-26", it's a date.
 */
function endsInDayRange({
  parts,
  between,
  after,
}: {
  parts: readonly string[];
  between: readonly [string, string];
  after: string;
}): boolean {
  if (parts[2]?.length !== 2) return false;
  const [monthToDay, dayToYear] = between;
  const joinedApart = /^\s?[-/]\s?$/.test(dayToYear) && monthToDay !== dayToYear;
  return joinedApart || YEAR_AFTER.test(after);
}

/**
 * Whether `before`, the text before a date's day or its weekday, ends in a range's first day: "14-" in "14-15 March".
 * Only where nothing but a range can be meant: the number starts the text, or follows its own weekday in `date`'s
 * month, "Fri 13 – Sat 14", or a word in lower case that numbers nothing, "held 13 – 15". After "Page 2 - " or
 * "Pt. 3 – " it's a label's.
 */
function followsRangeDay(before: string, date: CalendarDay): boolean {
  const day = RANGE_DAY_BEFORE.exec(before);
  if (day === null) return false;
  const ahead = before.slice(0, day.index).trimEnd();
  if (ahead === "") return true;
  const [, word = "", stop = ""] = WORD_ENDING.exec(ahead) ?? [];
  if (word === "") return false;
  const first = { ...date, day: Number(/\d+/.exec(day[0])?.[0]) };
  if (asWeekday(word, first) !== undefined) return true;
  const inLowerCase = stop === "" && word === word.toLowerCase();
  return inLowerCase && !NUMBERING_WORDS.has(word);
}

/** Whether a day-first date's two digit year is a range's last day, a month named after it: "14 March - 15 March". */
function yearIsNextDay({ parts, after }: { parts: readonly string[]; after: string }): boolean {
  const next = WORD_AFTER.exec(after)?.[1];
  return parts[2]?.length === 2 && next !== undefined && namesOf(next, "month").length > 0;
}

/** A date written in numbers alone: year first, or a day and month in the order they can only read in, or the document's. */
function numericDateOf(match: RegExpMatchArray, reading: DateReading): DateValue | undefined | typeof UNSETTLED {
  const [whole, before = "", beforeSpace = "", first = "", mark = "", second = "", last = ""] = match;
  const yearFirst = first.length === 4 && last.length <= 2;
  if (!yearFirst && (first.length > 2 || !isYear(last))) return undefined;
  const order = yearFirst ? "year-first" : (onlyOrderOf(Number(first), Number(second)) ?? reading.order);
  // Read either way and nothing settles which: an editor that guessed would write a wrong date that looks right.
  if (order === undefined) return undefined;
  const parts = numericPartsOf(order, { first, second, last });
  const date = dayOf({ year: parts.year, month: Number(parts.month), day: Number(parts.day) });
  if (date === undefined) return undefined;
  const weekday = asWeekday(before, date);
  const named = weekday === undefined ? undefined : settledWeekday(weekday, reading);
  if (named === UNSETTLED) return UNSETTLED;
  const format: DateFormat = {
    order: parts.order,
    between: [mark, mark],
    closing: "",
    digits: { ...numericDigitsOf(parts, { unshown: 2 }), year: digitsOf(parts.year) },
    month: null,
    ordinal: null,
    weekday: named === undefined ? null : weekdayBeforeOf(named, { before, beforeSpace }),
  };
  return located({ date, format }, { match, whole, before: weekday === undefined ? before + beforeSpace : "" });
}

/** A date in CJK's marks, its year always first, and its weekday after it where it has one. */
function cjkDateOf(match: RegExpExecArray, reading: DateReading): DateValue | undefined | typeof UNSETTLED {
  const [whole, year = "", afterYear = "", month = "", afterMonth = "", day = "", closing = ""] = match;
  const date = dayOf({ year, month: Number(month), day: Number(day) });
  if (date === undefined) return undefined;
  const end = match.index + whole.length;
  const format: DateFormat = {
    order: ["year", "month", "day"],
    between: [afterYear, afterMonth],
    closing,
    // Written with no zero unless the page wrote one, as Intl writes these.
    digits: { ...numericDigitsOf({ day, month }, { unshown: 1 }), year: 4 },
    month: null,
    ordinal: null,
    weekday: null,
  };
  const dated = withWeekdayAfter(
    { kind: "date", date, format, start: match.index, end },
    { text: match.input, reading },
  );
  if (dated === UNSETTLED) return UNSETTLED;
  // A range of days, "14日～15日": an editor would rewrite only its first.
  if (CJK_RANGE_AFTER.test(match.input.slice(dated.end))) return UNSETTLED;
  return dated;
}

/**
 * `value`, a CJK date in `text`, with the weekday written after it where it has one. Unsettled where a bracket
 * holds its weekday and more, or a bracketed weekday no language settles on; a word alone that none settles on,
 * as "to" in "2026年3月19日 to", is a word.
 */
function withWeekdayAfter(
  value: DateValue,
  { text, reading }: { text: string; reading: DateReading },
): DateValue | typeof UNSETTLED {
  const weekday = weekdayAfterIn(text.slice(value.end), value.date);
  if (weekday === undefined) return value;
  if (weekday === UNSETTLED) return UNSETTLED;
  const named = settledWeekday(weekday, reading);
  if (named === UNSETTLED) return weekday.bracketed ? UNSETTLED : value;
  const format = { ...value.format, weekday: weekdayAfterOf(named, weekday) };
  return { ...value, format, end: value.end + weekday.length };
}

/** A day as the page wrote it: its digits, and the ordinal it is where letters follow them ("14th"). */
function writtenDayOf(written: string): { digits: string; ordinal: DayOrdinal | undefined } {
  const digits = /^\d+/.exec(written)?.[0] ?? "";
  const suffix = written.slice(digits.length);
  return { digits, ordinal: suffix === "" ? undefined : { day: Number(digits), suffix } };
}

/**
 * The name of the weekday beside a date with no month named, in the one language it settles on. Unsettled where
 * none does: written for another date it would be a guess, and left as it is, stale.
 */
function settledWeekday(weekday: WeekdayWord, reading: DateReading): DateName | typeof UNSETTLED {
  return settledNames({ weekday, between: [] }, reading)?.weekday ?? UNSETTLED;
}

/** How the page wrote a weekday before its date: "Saturday, ". */
function weekdayBeforeOf(
  name: DateName,
  { before, beforeSpace }: { before: string; beforeSpace: string },
): WeekdayFormat {
  return { word: wordOf(name, before), side: "before", around: ["", beforeSpace] };
}

/** How the page wrote a weekday after its date: "(土)". */
function weekdayAfterOf(name: DateName, weekday: WeekdayAfter): WeekdayFormat {
  return { word: wordOf(name, weekday.word), side: "after", around: weekday.around };
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

/**
 * How many digits a numeric date's day and month are written in: two where a part shows a zero, one where it's a
 * single digit. A part that shows neither, as 14 doesn't, follows the other; with neither showing, `unshown`.
 */
function numericDigitsOf(
  { day, month }: { day: string; month: string },
  { unshown }: { unshown: 1 | 2 },
): { day: 1 | 2; month: 1 | 2 } {
  const dayShows = digitsShownBy(day);
  const monthShows = digitsShownBy(month);
  return { day: dayShows ?? monthShows ?? unshown, month: monthShows ?? dayShows ?? unshown };
}

/** The digits a number's own writing shows a date part takes: undefined where it's two, with no zero. */
function digitsShownBy(digits: string): 1 | 2 | undefined {
  if (digits.length === 1) return 1;
  if (digits.startsWith("0")) return 2;
  return undefined;
}

function digitsOf(year: string): 2 | 4 {
  return year.length === 2 ? 2 : 4;
}

function wordOf(name: DateName, written: string): NameWord {
  const stopLeftOff = name.name.endsWith(".") && !written.endsWith(".");
  const own = stopLeftOff ? name.name.slice(0, -1) : name.name;
  return { language: name.language, width: name.width, letterCase: caseOf(written, own), stopLeftOff };
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
  const ordinal = format.ordinal === null ? "" : suffixFor(date.day, format.ordinal);
  const parts = { day: String(date.day).padStart(digits.day, "0") + ordinal, month, year };
  const [first, second, third] = format.order;
  const written = parts[first] + format.between[0] + parts[second] + format.between[1] + parts[third] + format.closing;
  if (format.weekday === null) return written;
  const { word, side, around } = format.weekday;
  const weekday = around[0] + nameFor("weekday", weekdayOf(date), word) + around[1];
  return side === "before" ? weekday + written : written + weekday;
}

/**
 * A word beside a date, if some language names it the day of the week `date` falls on. Why: a word beside a
 * date that names another day isn't its weekday, but a word that happens to be one in some other language.
 */
function asWeekday(word: string, date: CalendarDay): WeekdayWord | undefined {
  const index = weekdayOf(date);
  return namesOf(word, "weekday").some((name) => name.index === index) ? { word, index } : undefined;
}

/**
 * The weekday written after a CJK date, if it names the date's own day: in brackets, "(土)", "(Sat)", or not, a
 * word alone, "Sat", or the start of a run of CJK letters, "土曜日まで". Unsettled where a bracket holds it and more.
 */
function weekdayAfterIn(rest: string, date: CalendarDay): WeekdayAfter | undefined | typeof UNSETTLED {
  const [, space = "", open = "", letters = "", stop = "", close = ""] = AFTER_CJK.exec(rest) ?? [];
  const weekday = CJK_LETTER.test(letters) ? longestWeekdayIn(letters, date) : asWeekday(letters, date);
  if (weekday === undefined) return undefined;
  const bracketed = open !== "";
  const fillsBracket = weekday.word === letters && close !== "";
  // A bracket holding more than its weekday, "(土・祝)": rewritten or left, the rest of it may no longer hold.
  if (bracketed && !fillsBracket) return UNSETTLED;
  // A full stop inside the bracket is the weekday's own, as "Sat." abbreviates: it stays as the page wrote it.
  const around = [space + open, bracketed ? stop + close : ""] as const;
  return { ...weekday, bracketed, around, length: around[0].length + weekday.word.length + around[1].length };
}

/**
 * The longest start of `letters` naming `date`'s weekday. One letter that runs on into more starts a word, as
 * 日 starts 日本, so it counts only alone.
 */
function longestWeekdayIn(letters: string, date: CalendarDay): WeekdayWord | undefined {
  for (let length = letters.length; length > 0; length--) {
    const wholeOrLong = length === letters.length || length > 1;
    const weekday = wholeOrLong ? asWeekday(letters.slice(0, length), date) : undefined;
    if (weekday !== undefined) return weekday;
  }
  return undefined;
}

/** The day of the week `date` falls on, from 0, Sunday. */
export function weekdayOf({ year, month, day }: CalendarDay): number {
  return new Date(Date.UTC(year, month - 1, day)).getUTCDay();
}

/** A language that names a date's words, and the name each word is in it. */
interface Named {
  readonly language: string;
  readonly month?: DateName;
  readonly weekday?: DateName;
}

/** A weekday's name as written beside a date, and the day it names there, from 0, Sunday. */
interface WeekdayWord {
  readonly word: string;
  readonly index: number;
}

/** A weekday written after a date: in brackets or not, what's either side of its name, and how long all three run. */
interface WeekdayAfter extends WeekdayWord {
  readonly bracketed: boolean;
  readonly around: readonly [string, string];
  readonly length: number;
}

/** A date's words: its month's and weekday's names as written, what's between its parts, and its day's ordinal. */
interface DateWords {
  readonly month?: string;
  readonly weekday?: WeekdayWord;
  readonly between: readonly string[];
  readonly ordinal?: DayOrdinal;
}

/**
 * The names a date's words are, in the one language they settle on; undefined where none does, so no editor guesses.
 *
 * A language counts if it names every month the document holds and this date's words, or, for a weekday beside a
 * month in numbers, where none does so, its words alone. Where those that count would write a month or a weekday
 * apart, the reader's languages decide among them.
 */
function settledNames(words: DateWords, reading: DateReading): Named | undefined {
  const naming = languagesNaming(words);
  const ofDocument = naming.filter((named) => reading.monthWords.every((word) => namesMonth(named, word)));
  const weekdayAlone = ofDocument.length === 0 && words.month === undefined;
  const counting = weekdayAlone ? naming : ofDocument;
  if (counting.length === 0) return undefined;
  if (writeAlike(counting, words)) return counting[0];
  return readersOf(counting, { words, reading });
}

/**
 * The languages naming every word in `words` and writing its day as the page did, a weekday only as the date's own
 * day, each name as written if it has one so.
 */
function languagesNaming(words: DateWords): Named[] {
  const kinds = kindsIn(words);
  const byLanguage = new Map<string, Partial<Record<NameKind, DateName[]>>>();
  for (const kind of kinds) {
    for (const name of namesIn(words, kind)) {
      const found = byLanguage.get(name.language) ?? {};
      found[kind] = [...(found[kind] ?? []), name];
      byLanguage.set(name.language, found);
    }
  }
  const all = [...byLanguage].flatMap(([language, found]) => {
    if (!kinds.every((kind) => found[kind] !== undefined)) return [];
    const pick = (kind: NameKind) => bestOf(found[kind] ?? [], wordIn(words, kind));
    return [{ language, month: pick("month"), weekday: pick("weekday") }];
  });
  return all.filter((named) => writesBetween(named, words.between) && writesDay(named, words.ordinal));
}

/** Whether `named`'s language writes the day as the page did, where the page wrote it as an ordinal ("14th"). */
function writesDay(named: Named, ordinal: DayOrdinal | undefined): boolean {
  return ordinal === undefined || writesOrdinal(named.language, ordinal);
}

/** Which of a month and a weekday `words` names. */
function kindsIn(words: DateWords): NameKind[] {
  return (["month", "weekday"] as const).filter((kind) => words[kind] !== undefined);
}

/** The word written for `kind` in a date: its month's or its weekday's name. */
function wordIn(words: DateWords, kind: NameKind): string {
  return kind === "month" ? (words.month ?? "") : (words.weekday?.word ?? "");
}

/** Every name the word written for `kind` is, a weekday's only where it names the date's own day. */
function namesIn(words: DateWords, kind: NameKind): readonly DateName[] {
  const names = namesOf(wordIn(words, kind), kind);
  if (kind === "month") return names;
  return names.filter((name) => name.index === words.weekday?.index);
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

/** Whether every one of `languages` writes each month and weekday the way the others do, the page's way. */
function writeAlike(languages: readonly Named[], words: DateWords): boolean {
  return kindsIn(words).every((kind) => {
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
  return nameFor(kind, index, wordOf(name, wordIn(words, kind)));
}

/** Whether `named`'s language names `word` a month. */
function namesMonth(named: Named, word: string): boolean {
  return namesOf(word, "month").some((name) => name.language === named.language);
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
